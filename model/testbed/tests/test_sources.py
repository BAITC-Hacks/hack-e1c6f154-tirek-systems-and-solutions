"""Independent, invented OOXML fixtures for the source-inspection boundary."""
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from xml.etree import ElementTree as ET
from zipfile import ZipFile, ZIP_DEFLATED

from model.testbed.sources import inspect_sources

MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
DETAIL_NAME = "IEK/Динамика продаж_2025-2026.xlsx"
MOQ_NAME = "IEK/MOQ  ИЭК.xlsx"
HEADERS = dict(zip("ABCDEFGH", ("Дата", "Номер", "Документ", "Код", "Номенклатура", "Ед.", "Склад", "Количество")))


def workbook(rows, formula_cells=None, external=False, second_sheet=False):
    """Build tiny fixture bytes directly; no actual source rows or identifiers."""
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as z:
        wb = ET.Element("workbook", xmlns=MAIN)
        sheets = ET.SubElement(wb, "sheets")
        ET.SubElement(sheets, "sheet", name="private_sheet_name", sheetId="1", attrib={f"{{{REL}}}id": "rId1"})
        relationships = ET.Element("Relationships", xmlns="http://schemas.openxmlformats.org/package/2006/relationships")
        ET.SubElement(relationships, "Relationship", Id="rId1", Target="worksheets/sheet1.xml", Type=REL+"/worksheet")
        if second_sheet:
            ET.SubElement(sheets, "sheet", name="Sheet2", sheetId="2", attrib={f"{{{REL}}}id": "rId2"})
            ET.SubElement(relationships, "Relationship", Id="rId2", Target="worksheets/sheet2.xml", Type=REL+"/worksheet")
            z.writestr("xl/worksheets/sheet2.xml", f'<worksheet xmlns="{MAIN}"><sheetData><row r="3"><c r="A3" t="inlineStr"><is><t>год</t></is></c><c r="N3" t="inlineStr"><is><t>ИТОГО</t></is></c></row></sheetData></worksheet>')
        z.writestr("xl/workbook.xml", ET.tostring(wb))
        z.writestr("xl/_rels/workbook.xml.rels", ET.tostring(relationships))
        ws = ET.Element("worksheet", xmlns=MAIN)
        data = ET.SubElement(ws, "sheetData")
        for row_number, cells in sorted(rows.items()):
            row = ET.SubElement(data, "row", r=str(row_number))
            for col, value in cells.items():
                address = f"{col}{row_number}"
                c = ET.SubElement(row, "c", r=address)
                if formula_cells and address in formula_cells:
                    formula, cache, typ = formula_cells[address]
                    c.set("t", typ)
                    ET.SubElement(c, "f").text = formula
                    if cache is not None:
                        ET.SubElement(c, "v").text = str(cache)
                elif isinstance(value, (float, int)):
                    ET.SubElement(c, "v").text = str(value)
                elif value is not None:
                    c.set("t", "inlineStr")
                    ET.SubElement(ET.SubElement(c, "is"), "t").text = value
        z.writestr("xl/worksheets/sheet1.xml", ET.tostring(ws))
        if external:
            z.writestr("xl/externalLinks/externalLink1.xml", "<externalLink/>")
    return output.getvalue()


class SourceInspectionTests(unittest.TestCase):
    def inspect(self, entries, archive="IEK.zip"):
        with tempfile.TemporaryDirectory() as temp:
            with ZipFile(Path(temp)/archive, "w", ZIP_DEFLATED) as z:
                for name, data in entries.items():
                    z.writestr(name, data)
            return inspect_sources(temp)

    def detail(self, quantity=-5, unit="м"):
        row = {"A": "20.07.2023 16:15:38", "B": "private_document_id", "C": "private_document_kind",
               "D": "private_sku", "E": "private_product_name", "F": unit,
               "G": "private_warehouse", "H": quantity}
        return row

    def test_missing_is_not_zero_negative_is_not_absolute_and_units_preserved(self):
        rows = {1: HEADERS, 2: self.detail(-5, "м"), 3: self.detail(None, "упак"),
                4: self.detail(0, "шт"), 5: self.detail(2.5, "м")}
        rows[3]["A"] = "1.1.2024 9:05:03"
        report = self.inspect({DETAIL_NAME: workbook(rows)})
        source = next(f for f in report["files"] if f["file"] == DETAIL_NAME)
        sheet = source["sheets"][0]
        self.assertEqual(source["schema_status"], "matched")
        self.assertEqual(sheet["counts"]["quantity_missing_or_invalid"], 1)
        self.assertEqual(sheet["counts"]["negative_quantity_rows"], 1)
        self.assertEqual(sheet["counts"]["zero_quantity_rows"], 1)
        self.assertEqual(sheet["counts"]["fractional_quantity_rows"], 1)
        self.assertEqual(sheet["units"], {"м": 2, "упак": 1, "шт": 1})
        self.assertEqual(sheet["date_start"], "2023-07-20T16:15:38")
        self.assertEqual(sheet["date_end"], "2024-01-01T09:05:03")
        self.assertEqual(sheet["counts"]["dated_rows"], 4)
        self.assertEqual(sheet["numeric_columns"]["H"]["absent_or_non_numeric"], 1)

    def test_cached_error_missing_cache_and_external_reference_are_distinct(self):
        rows = {1: {"A": "№", "B": "Код 1с", "C": "Артикул поставщика", "D": "Наименование", "E": "Мин. разр. к отгр."},
                2: {"B": "private_sku_a", "E": None}, 3: {"B": "private_sku_b", "E": None},
                4: {"B": "private_sku_c", "E": None}}
        formulas = {"E2": ("VLOOKUP(C2,[1]Sheet1!A:B,2,0)", "#N/A", "e"),
                    "E3": ("1+1", None, "n"), "E4": ("0", 0, "n")}
        report = self.inspect({MOQ_NAME: workbook(rows, formulas, external=True)})
        source = next(f for f in report["files"] if f["file"] == MOQ_NAME)
        sheet = source["sheets"][0]
        self.assertEqual(source["external_link_count"], 1)
        self.assertEqual(sheet["counts"]["formula_cells"], 3)
        self.assertEqual(sheet["counts"]["formula_cached_errors"], 1)
        self.assertEqual(sheet["counts"]["formula_cache_absent_or_empty"], 1)
        self.assertEqual(sheet["excel_errors_first_100"], [{"cell": "E2", "error": "#N/A"}])
        self.assertEqual(sheet["formula_cache_missing_first_100"], ["E3"])
        self.assertEqual(sheet["numeric_columns"]["E"]["zero"], 1)

    def test_no_invented_customer_availability_lead_or_latent_wape(self):
        report = self.inspect({DETAIL_NAME: workbook({1: HEADERS, 2: self.detail()})})
        for brand in ("IEK", "SE"):
            fields = report["readiness"][brand]["fields"]
            for name in ("pseudonymous_customer_id", "exact_stockout_intervals", "new_order_lead_time", "sales_price", "external_future_growth_forecast"):
                self.assertEqual(fields[name], "not_present")
            self.assertNotEqual(report["readiness"][brand]["status"], "ready")
        self.assertFalse(report["forecast_evaluation"]["latent_regular_wape_available"])
        self.assertEqual(report["missing_changed_or_invalid_workbooks"], 11)

    def test_private_values_and_arbitrary_sheet_names_never_emitted(self):
        report = self.inspect({DETAIL_NAME: workbook({1: HEADERS, 2: self.detail()})})
        encoded = json.dumps(report, ensure_ascii=False)
        for secret in ("private_document_id", "private_document_kind", "private_sku", "private_product_name", "private_warehouse", "private_sheet_name"):
            self.assertNotIn(secret, encoded)
        source = next(f for f in report["files"] if f["file"] == DETAIL_NAME)
        self.assertRegex(source["sha256"], r"^[a-f0-9]{64}$")

    def test_changed_schema_is_retained_as_mismatch_not_interpreted_as_zero(self):
        header = {**HEADERS, "H": "private_unexpected_header"}
        report = self.inspect({DETAIL_NAME: workbook({1: header, 2: self.detail()})})
        source = next(f for f in report["files"] if f["file"] == DETAIL_NAME)
        self.assertEqual(source["schema_status"], "mismatch")
        self.assertEqual(source["sheets"][0]["schema_errors"][0]["cell"], "H1")
        self.assertNotIn("private_unexpected_header", json.dumps(report))
        self.assertEqual(report["readiness"]["IEK"]["fields"]["observed_sales"], "unavailable_due_to_missing_or_changed_schema")

    def test_multiplicity_does_not_establish_supplier_moq(self):
        name = "Systeme electric/MOQ SystemElectric.xlsx"
        rows = {1: {"A": "№", "B": "Номенклатура", "C": "Номенклатура.Код", "D": "Артикул", "E": "Кратность"},
                2: {"C": "private_sku", "E": 5}}
        report = self.inspect({name: workbook(rows)}, archive="Systeme electric.zip")
        self.assertEqual(report["readiness"]["SE"]["fields"]["order_multiple"], "detected")
        self.assertEqual(report["readiness"]["SE"]["fields"]["supplier_moq"], "not_present")

    def test_existing_order_eta_is_safe_but_not_new_order_lead(self):
        headers = {"A": "Код 1с", "B": "Артикул ИЭК", "C": "Наименование"}
        headers.update({c: "private_document_id (поступление до 10.10.2026)" for c in "DEFGHI"})
        name = "IEK/Путь ИЭК 22.09.2026.xlsx"
        report = self.inspect({name: workbook({1: headers, 2: {"A": "private_sku", "D": 10}})})
        source = next(f for f in report["files"] if f["file"] == name)
        self.assertEqual(source["schema_status"], "matched")
        self.assertEqual(source["sheets"][0]["existing_order_eta"][0], {"column": "D", "eta": "2026-10-10"})
        self.assertNotIn("private_document_id", json.dumps(report))
        self.assertEqual(report["readiness"]["IEK"]["fields"]["new_order_lead_time"], "not_present")

    def test_corrupt_archive_and_empty_input_are_not_success(self):
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp)/"IEK.zip").write_bytes(b"not a zip")
            report = inspect_sources(temp)
            self.assertEqual(report["inspected_workbooks"], 0)
            self.assertEqual(report["missing_changed_or_invalid_workbooks"], 12)
        with tempfile.TemporaryDirectory() as temp:
            report = inspect_sources(temp)
            self.assertEqual(report["missing_changed_or_invalid_workbooks"], 12)

    def test_monthly_zero_stock_is_not_an_exact_stockout_interval(self):
        from model.testbed.sources import _column
        month_names = ("янв.", "февр.", "март", "апр.", "май", "июнь", "июль", "авг.", "сент.", "окт.", "нояб.", "дек.")
        header = {"A": "Номенклатура", "B": "Ед.", "C": "Номенклатура.Код"}
        row = {"A": "private_product", "B": "м", "C": "private_sku"}
        for index in range(33):
            col = _column(index+4)
            header[col] = f"{month_names[index%12]} {2024+index//12}"
            row[col] = 0
        name = "IEK/Ежемесячные остатки продукции за последние 2 года  ИЭК.xlsx"
        report = self.inspect({name: workbook({1: header, 2: row})})
        source = next(f for f in report["files"] if f["file"] == name)
        self.assertEqual(source["schema_status"], "matched")
        self.assertEqual(report["readiness"]["IEK"]["fields"]["monthly_stock_snapshots"], "detected")
        self.assertEqual(report["readiness"]["IEK"]["fields"]["exact_stockout_intervals"], "not_present")
        self.assertEqual(len(source["sheets"][0]["monthly_columns"]), 33)

    def test_thirteen_month_formula_under_twelve_month_label_is_flagged(self):
        header = {"A": "№", "B": "Артикул поставщика", "C": "Код 1с", "D": "Наименование", "E": "Категория 2026",
                  "F": "СС реал", "AP": "Сумма последние 12 мес", "AQ": "Ср мес за последние 12 мес",
                  "AR": "Кэф. Роста", "AS": "Кэф. Сез-ти", "AX": "Остаток", "AY": "Зарезервировано",
                  "AZ": "Свободный остаток", "BA": "Запас", "BB": "Заказ", "BC": "СЭ в пути 24.09"}
        name = "Systeme electric/Товар в пути_SystemElectric на 22.09.2026.xlsx"
        rows = {2: header, 3: {"C": "private_sku", "AP": None}}
        data = workbook(rows, {"AP3": ("SUM(AC3:AO3)", 130, "n")}, second_sheet=True)
        report = self.inspect({name: data}, archive="Systeme electric.zip")
        source = next(f for f in report["files"] if f["file"] == name)
        self.assertEqual(source["schema_status"], "matched")
        self.assertEqual(source["sheets"][0]["formula_observations"][0]["issue"], "label_says_12_months_but_formula_sums_13_columns")
        self.assertEqual(report["readiness"]["SE"]["fields"]["external_future_growth_forecast"], "not_present")


if __name__ == "__main__":
    unittest.main()
