"""Independent numeric oracles and tampering tests for real-data evidence."""
from copy import deepcopy
import csv
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from model.testbed.partner_io import number, private_path, csv_records
from model.testbed.real_evidence import metric, observed_sales, past_group, verify_table, verify_panel_set
from model.testbed.real_bridge import build_packet, check_forecasts, constraints, csv_safe, stock_snapshot


def tiny_xlsx(path, rows):
    """Minimal independent source workbook with inline strings."""
    from xml.sax.saxutils import escape
    sheet = '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
    for i, row in enumerate(rows, 1):
        sheet += f'<row r="{i}">'
        for column, value in row.items():
            sheet += f'<c r="{column}{i}" t="inlineStr"><is><t>{escape(str(value))}</t></is></c>'
        sheet += '</row>'
    sheet += '</sheetData></worksheet>'
    with ZipFile(path, 'w') as book:
        book.writestr('xl/workbook.xml', '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="data" sheetId="1" r:id="id1"/></sheets></workbook>')
        book.writestr('xl/_rels/workbook.xml.rels', '<Relationships><Relationship Id="id1" Target="worksheets/sheet1.xml"/></Relationships>')
        book.writestr('xl/worksheets/sheet1.xml', sheet)


class RealEvidenceTests(unittest.TestCase):
    def test_decimal_wape_mae_bias_zero_denominator_and_rows(self):
        rows = [{'actual': Decimal(a), 'predicted': Decimal(p)} for a, p in [('10', '8'), ('0', '3'), ('5', '9')]]
        result = metric(rows)
        self.assertEqual(result['absolute_error'], 9)
        self.assertEqual(result['wape'], Decimal('0.6'))
        self.assertEqual(result['mae'], 3)
        self.assertEqual(result['bias_units'], 5)
        self.assertEqual(result['zero_actual_rows'], 1)
        self.assertEqual(result['absolute_error_on_zero_actual'], 3)
        empty = metric([{'actual': Decimal(0), 'predicted': Decimal(4)}])
        self.assertIsNone(empty['wape'])
        self.assertEqual(empty['mae'], 4)

    def test_reconstructs_only_positive_invoices_with_document_not_client(self):
        header = dict(zip('ABCDEFGH', ['Дата', 'Номер', 'Документ', 'Код', 'Номенклатура', 'Ед.', 'Склад', 'Количество']))
        template = dict(zip('ABCDEFGH', ['05.05.2026 12:00:00', 'invoice', 'Расходная накладная test', 'synthetic-code', 'fixture', 'шт', 'warehouse', '2.5']))
        rows = [header, template, {**template, 'H': '-1'}, {**template, 'C': 'Возврат', 'H': '8'},
                {**template, 'A': '05.05.2024 12:00:00', 'H': '99'}, {**template, 'H': '0.5'}]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'tiny.xlsx'; tiny_xlsx(path, rows)
            series, counts = observed_sales(path)
        self.assertEqual(series, {('шт', 'synthetic-code'): {date(2026, 5, 5): Decimal(3)}})
        self.assertEqual(counts['negative_rows'], 1)
        self.assertEqual(counts['used_positive_invoice_rows'], 2)

    def table_fixture(self):
        origin = date(2026, 5, 4)
        series = {('шт', 'old'): {date(2025, 10, 1): Decimal(10)},
                  ('шт', 'future'): {origin + timedelta(days=1): Decimal(7)}}
        rows = [{'sku': 'old', 'origin': str(origin), 'target': '0', 'group': 'inactive', 'abc': 'A', 'method': '2', 'selected': '2'},
                {'sku': 'future', 'origin': str(origin), 'target': '7', 'group': 'unseen', 'abc': 'unseen', 'method': '0', 'selected': '0'}]
        return series, rows

    def check_table(self, rows, series):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'predictions.csv'
            with path.open('w', newline='') as handle:
                writer = csv.DictWriter(handle, fieldnames=['sku', 'origin', 'target', 'group', 'abc', 'method', 'selected'])
                writer.writeheader(); writer.writerows(rows)
            return verify_table(path, series, 'шт', ['2026-05-04'], 'method', True)

    def test_full_universe_preserves_zero_and_unseen(self):
        series, rows = self.table_fixture()
        result = metric(self.check_table(rows, series))
        self.assertEqual(result['sku_windows'], 2)
        self.assertEqual(result['absolute_error'], 9)
        self.assertEqual(result['actual_quantity'], 7)

    def test_csv_tampering_missing_duplicate_target_group_and_predictions_rejected(self):
        series, rows = self.table_fixture()
        mutations = [rows[:1], rows[1:], rows + rows[:1]]
        for index, field, value in [(0, 'target', '1'), (0, 'group', 'regular'), (0, 'abc', 'B'),
                                    (0, 'selected', '9'), (0, 'method', 'NaN'), (0, 'method', '-1'),
                                    (1, 'method', '4'), (1, 'origin', '2026-06-01')]:
            change = deepcopy(rows); change[index][field] = value; mutations.append(change)
        for change in mutations:
            with self.subTest(mutation=change):
                with self.assertRaises(ValueError):
                    self.check_table(change, series)

    def test_group_ignores_future_sales(self):
        origin = date(2026, 5, 4)
        old = {origin - timedelta(days=i): Decimal(10) for i in range(200)}
        self.assertEqual(past_group(old, origin), 'regular')
        self.assertEqual(past_group({**old, origin + timedelta(days=1): Decimal('1e50')}, origin), 'regular')

    def test_panel_omission_cannot_turn_all_empty_into_target_success(self):
        series = {'SE': {('шт', 'fixture'): {date(2026, 5, 4): Decimal(1)}}}
        panels = {'SE__pieces': {'supplier': 'SE', 'unit': 'шт'}}
        verify_panel_set(series, panels)
        for bad in ({}, {'SE__pieces': {'supplier': 'IEK', 'unit': 'шт'}}, {**panels, 'extra': panels['SE__pieces']}):
            with self.assertRaises(ValueError): verify_panel_set(series, bad)

    def test_duplicate_headers_and_ragged_records_cannot_hide_numbers(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'predictions.csv'
            for text in ['model,model\n999,5\n', 'model,target\n5\n', 'model,target\n5,5,999\n', 'target\n5\n']:
                path.write_text(text)
                with self.assertRaises(ValueError): csv_records(path, ['model', 'target'])


class RealBridgeTests(unittest.TestCase):
    def forecast_fixture(self):
        row = {'supplier': 'SE', 'unit': 'шт', 'sku': 'fixture', 'origin': '2026-09-21', 'warehouse': 'warehouse',
               'selected_method': 'method', 'forecast': '280', 'forecast_start': '2026-09-22', 'forecast_end': '2026-10-19'}
        policy = {'panels': {'SE__pieces': {'selected': 'method', 'trained_as_of': '2026-09-21'}}}
        return row, policy

    def test_forecast_dates_backcast_duplicate_nonfinite_and_method_rejected(self):
        row, policy = self.forecast_fixture()
        cases = [(row, False), ({**row, 'origin': '2026-08-24'}, True), ({**row, 'forecast_end': '2026-10-20'}, True),
                 ({**row, 'forecast': 'NaN'}, True), ({**row, 'selected_method': 'other'}, True),
                 ({**row, 'forecast': '-1'}, True)]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'forecast.csv'
            for changed, fails in cases:
                with path.open('w', newline='') as handle:
                    writer = csv.DictWriter(handle, fieldnames=row); writer.writeheader(); writer.writerow(changed)
                if fails:
                    with self.assertRaises(ValueError): check_forecasts(path, policy, policy)
                else:
                    self.assertEqual(len(check_forecasts(path, policy, policy)), 1)
            with path.open('w', newline='') as handle:
                writer = csv.DictWriter(handle, fieldnames=row); writer.writeheader(); writer.writerows([row, row])
            with self.assertRaises(ValueError): check_forecasts(path, policy, policy)

    def test_no_invented_distribution_policy_or_order_despite_matched_stock(self):
        row, _ = self.forecast_fixture()
        snapshot = {'declared_snapshot_date': '2026-09-22', 'invalid_fields': [], 'net_equals_gross_minus_reserve': True}
        packet = build_packet([row], {'SE': {'fixture': [{'raw_value_valid': True, 'field': 'order_multiple', 'value': '5'}]}}, {'fixture': snapshot})
        result = packet['suppliers'][0]['items'][0]
        self.assertIsNone(result['quantity'])
        self.assertTrue(result['approval_blocked'])
        self.assertIn('snapshot_after_forecast_origin', result['missing'])
        self.assertIn('minimum_order_quantity_unknown', result['missing'])
        self.assertIn('demand_uncertainty_not_calibrated', result['missing'])
        self.assertNotIn('horizon_distribution', result)
        self.assertNotIn('availability_fraction', result)

    def test_moq_and_multiple_not_conflated_and_duplicates_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'constraint.xlsx'
            tiny_xlsx(path, [{'B': 'Код 1с', 'E': 'Мин. разр. к отгр.'}, {'B': 'fixture', 'E': '6'}, {'B': 'fixture', 'E': '12'}])
            result = constraints(path, 'IEK')['fixture']
            self.assertEqual(len(result), 2)
            self.assertTrue(all(r['field'] == 'min_order_qty' for r in result))
            self.assertTrue(all(r['unit_confirmed'] is False for r in result))
            with self.assertRaises(ValueError): constraints(path, 'SE')

    def test_missing_header_rows_never_enable_positional_guessing(self):
        with patch('model.testbed.real_bridge.workbook_rows', return_value=iter([(2, {'B': 'fixture', 'E': '6'})])):
            with self.assertRaises(ValueError): constraints('unused', 'IEK')
        with patch('model.testbed.real_bridge.workbook_rows', return_value=iter([(3, {'C': 'fixture', 'AX': '10', 'AY': '0', 'AZ': '10', 'BC': '1'})])):
            with self.assertRaises(ValueError): stock_snapshot('unused')
        with patch('model.testbed.real_evidence.workbook_rows', return_value=iter([(2, {'A': '01.01.2026 00:00:00'})])):
            with self.assertRaises(ValueError): observed_sales('unused')

    def test_csv_formula_escape_and_private_output_guard(self):
        self.assertEqual(csv_safe('=2+2'), "'=2+2")
        self.assertEqual(csv_safe('280'), '280')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / 'repo').mkdir(); (root / 'repo/.git').write_text('gitdir: anywhere')
            (root / 'link').symlink_to(root / 'repo', target_is_directory=True)
            with self.assertRaises(ValueError): private_path(root / 'repo/raw.csv')
            with self.assertRaises(ValueError): private_path(root / 'link/raw.csv')
            self.assertEqual(private_path(root / 'private/raw.csv'), root / 'private/raw.csv')
            (root / 'private').mkdir(); (root / 'private/.git').mkdir()
            self.assertEqual(private_path(root / 'private/raw.csv'), root / 'private/raw.csv')
        for value in [None, True, 'NaN', '-Infinity', '-1']:
            with self.assertRaises(ValueError): number(value)


if __name__ == '__main__':
    unittest.main()
