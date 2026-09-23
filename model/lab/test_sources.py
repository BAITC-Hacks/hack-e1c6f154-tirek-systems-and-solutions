"""Synthetic workbook and temporal invariance tests; no private workbook required."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

import numpy as np
import pandas as pd
from openpyxl import Workbook

from model.lab.sources import (
    _read_current, _read_moq, _read_turnover, current_profiles_at,
    enrich_features, load_se, monthly_prior_window, number, read_monthly,
)


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def workbook(self, name, rows):
        path = self.directory / name
        book = Workbook()
        for row in rows:
            book.active.append(row)
        book.save(path)
        book.close()
        return path

    def test_sales_preserve_codes_and_exclude_negative_without_abs(self):
        path = self.workbook("sales.xlsx", [
            ["Номенклатура", "Номенклатура.Код", "Артикул", "Кратность", "янв. 2024", "февр. 2024"],
            [None, None, None, None, "Количество", "Количество"],
            ["A", "001_", "A", 1, None, -4],
            ["B", "B02", "B", 1, 3, 0],
            ["Итого", None, None, None, 3, -4],
        ])
        table, audit = read_monthly(path, 1, True)
        self.assertEqual(list(table.index), ["001_", "B02"])
        self.assertEqual(table.loc["001_", pd.Timestamp("2024-01-01")], 0)
        self.assertTrue(np.isnan(table.loc["001_", pd.Timestamp("2024-02-01")]))
        self.assertEqual(audit["negative_cells_excluded"], 1)
        self.assertEqual(audit["header_footer_rows_excluded"], 2)

    def test_stock_blank_remains_unknown_and_explicit_zero_survives(self):
        path = self.workbook("stock.xlsx", [
            ["№", "Номенклатура", "Номенклатура.Код", "Ед.изм", "янв. 2024", "февр. 2024"],
            [1, "A", "001", "шт", None, 0],
        ])
        table, audit = read_monthly(path, 2, False)
        self.assertTrue(np.isnan(table.iloc[0, 0]))
        self.assertEqual(table.iloc[0, 1], 0)
        self.assertEqual(audit["unknown_cells"], 1)

    def test_duplicate_master_rows_require_evidence(self):
        header = ["Номенклатура", "Номенклатура.Код", "янв. 2024"]
        row = ["A", "001", 5]
        path = self.workbook("same.xlsx", [header, row, row])
        table, audit = read_monthly(path, 1, True)
        self.assertEqual(len(table), 1)
        self.assertEqual(audit["identical_duplicate_rows_excluded"], 1)
        path = self.workbook("conflict.xlsx", [header, row, ["A", "001", 6]])
        with self.assertRaisesRegex(ValueError, "Conflicting rows"):
            read_monthly(path, 1, True)

    def test_numeric_sku_is_rejected_instead_of_losing_zeroes(self):
        path = self.workbook("numeric.xlsx", [["Номенклатура", "Номенклатура.Код", "янв. 2024"], ["A", 1, 5]])
        with self.assertRaisesRegex(ValueError, "must be text"):
            read_monthly(path, 1, True)

    def test_ambiguous_schema_or_duplicate_periods_rejected(self):
        for name, headers in (("wrong.xlsx", ["Номенклатура.Код", "Номенклатура", "янв. 2024"]),
                              ("months.xlsx", ["Номенклатура", "Номенклатура.Код", "янв. 2024", "январь 2024"])):
            with self.subTest(name=name):
                path = self.workbook(name, [headers])
                with self.assertRaises(ValueError):
                    read_monthly(path, 1, True)

    def test_invalid_nonblank_values_do_not_become_zero(self):
        path = self.workbook("invalid.xlsx", [["Номенклатура", "Номенклатура.Код", "янв. 2024"], ["A", "001", "нет данных"]])
        table, audit = read_monthly(path, 1, True)
        self.assertTrue(np.isnan(table.iloc[0, 0]))
        self.assertEqual(audit["invalid_nonblank_cells"], 1)
        self.assertIsNone(number(True))
        self.assertIsNone(number(float("inf")))

    def test_multiples_are_not_fabricated_from_missing_or_zero(self):
        path = self.workbook("MOQ.xlsx", [["№", "Номенклатура", "Номенклатура.Код", "Артикул", "Кратность"],
                                         [1, "A", "001", "A", 12], [2, "B", "002", "B", 0],
                                         [3, "C", "003", "C", None]])
        multiples, audit = _read_moq(path)
        self.assertEqual(multiples, {"001": 12, "002": None, "003": None})
        self.assertEqual(audit["nonpositive_or_missing_multiple"], 2)

    def test_snapshot_consistency_and_unknown_economics(self):
        headers = [None] * 55
        for index, value in {2: "Код 1с", 4: "Категория 2026", 49: "Остаток", 50: "Зарезервировано", 51: "Свободный остаток", 54: "СЭ в пути 24.09"}.items():
            headers[index] = value
        row = [None] * 55
        for index, value in {2: "001", 4: "7", 5: 1234, 49: 50, 50: 10, 51: 35, 54: 12}.items():
            row[index] = value
        path = self.workbook("Товар на 22.09.2026.xlsx", [[None], headers, row])
        current, audit = _read_current(path, {"001": 12}, {})
        profile = current["001"]
        self.assertIsNone(profile["unit"])
        self.assertIsNone(profile["unit_cost"])
        self.assertIsNone(profile["lead_time_days"])
        self.assertIsNone(profile["min_order_qty"])
        self.assertEqual(profile["reported_cost_unverified"], 1234)
        self.assertEqual(profile["inbound_eta"], "2026-09-24")
        self.assertFalse(profile["free_stock_consistent"])
        self.assertEqual(audit["free_stock_identity_conflicts"], 1)

    def test_seasonality_does_not_read_precomputed_cross_year_coefficients(self):
        path = self.workbook("season.xlsx", [["год"] + list(range(1, 13)), [2024] + [100] * 12,
                                            [None, "янв", 9999999], [2025] + [200] * 12])
        turnover = _read_turnover(path)
        self.assertEqual(len(turnover), 24)
        self.assertEqual(turnover[pd.Timestamp("2024-01-01")], 100)

    def test_explicit_inventory_moq_and_warehouse_enable_verified_profile(self):
        headers = [None] * 57
        for index, value in {2: "Код 1с", 4: "Категория 2026", 49: "Остаток", 50: "Зарезервировано",
                             51: "Свободный остаток", 54: "СЭ в пути 24.09", 55: "Минимальная партия", 56: "Склад"}.items():
            headers[index] = value
        row = [None] * 57
        for index, value in {2: "001", 49: 50, 50: 10, 51: 40, 54: 12, 55: 24, 56: "almaty"}.items():
            row[index] = value
        path = self.workbook("Товар на 22.09.2026.xlsx", [[None], headers, row])
        current, audit = _read_current(path, {"001": 12}, {"001": "шт"})
        self.assertEqual(current["001"]["min_order_qty"], 24)
        self.assertTrue(current["001"]["warehouse_scope_verified"])
        self.assertEqual(audit["missing_min_order_qty"], 0)
        self.assertEqual(audit["unverified_warehouse_scope"], 0)
        row[55] = -1
        path = self.workbook("Товар на 22.09.2026.xlsx", [[None], headers, row])
        with self.assertRaisesRegex(ValueError, "minimum order"):
            _read_current(path, {"001": 12}, {"001": "шт"})

    def test_file_role_selection_rejects_missing_or_ambiguous_files(self):
        with self.assertRaisesRegex(ValueError, "Expected one file"):
            load_se(self.directory)
        self.workbook("Динамика1.xlsx", [["x"]])
        self.workbook("Динамика2.xlsx", [["x"]])
        with self.assertRaisesRegex(ValueError, "found 2"):
            load_se(self.directory)


class TemporalFeatureTests(unittest.TestCase):
    def sources(self):
        months = pd.date_range("2024-01-01", "2026-12-01", freq="MS")
        return {"monthly": pd.DataFrame([np.arange(1, len(months) + 1) * 30], index=["001"], columns=months),
                "monthly_stock": pd.DataFrame([np.arange(1, len(months) + 1)], index=["001"], columns=months),
                "turnover": {month: float(month.month) for month in months},
                "current": {"001": {"inventory_as_of": "2026-09-22", "on_hand": 50}}, "moq": {"001": 12}}

    def frame(self, origins):
        frame = pd.DataFrame({"sku": ["001"] * len(origins), "origin": pd.to_datetime(origins)})
        for field in ("sum7", "sum14", "sum28", "sum56", "sum84", "robust_sum28", "prior_year28"):
            frame[field] = 84.0
        return frame

    def test_future_sources_and_current_profiles_cannot_change_past_features(self):
        frame = self.frame(["2025-06-15"])
        before = self.sources()
        changed = deepcopy(before)
        for name in ("monthly", "monthly_stock"):
            changed[name].loc[:, changed[name].columns >= pd.Timestamp("2025-06-01")] = 1000000
        changed["turnover"].update({key: 1000000 for key in changed["turnover"] if key >= pd.Timestamp("2025-01-01")})
        changed["current"] = {"001": {"inventory_as_of": "2025-01-01", "on_hand": 99999999, "category": "2"}}
        changed["moq"] = {"001": 99999}
        pd.testing.assert_frame_equal(enrich_features(frame, before), enrich_features(frame, changed))

    def test_mixed_origins_use_their_own_completed_month(self):
        frame = self.frame(["2025-02-10", "2025-03-10"])
        result = enrich_features(frame, self.sources())
        self.assertEqual(list(result["last_month_stock"]), [13, 14])
        for index in range(2):
            one = enrich_features(frame.iloc[[index]], self.sources())
            pd.testing.assert_frame_equal(result.iloc[[index]], one)

    def test_partial_missing_month_gives_unknown_prior_year_window(self):
        sources = self.sources()
        sources["monthly"].loc["001", pd.Timestamp("2024-02-01")] = np.nan
        result = monthly_prior_window(sources["monthly"], ["001"], "2025-01-31")
        self.assertTrue(np.isnan(result[0]))
        result = monthly_prior_window(sources["monthly"], ["missing"], "2025-01-31")
        self.assertTrue(np.isnan(result[0]))

    def test_extended_horizon_cannot_read_months_unavailable_at_origin(self):
        sources = self.sources()
        result = monthly_prior_window(sources["monthly"], ["001"], "2025-01-31", horizon=400)
        self.assertTrue(np.isnan(result[0]))
        with self.assertRaises(ValueError):
            monthly_prior_window(sources["monthly"], ["001"], "2025-01-31", horizon=0)

    def test_leap_year_month_is_only_prorated_feature_not_daily_history(self):
        sources = self.sources()
        before = sources["monthly"].copy(deep=True)
        result = monthly_prior_window(sources["monthly"], ["001"], "2025-01-31")
        self.assertAlmostEqual(result[0], 60 * 28 / 29)
        pd.testing.assert_frame_equal(before, sources["monthly"])

    def test_missing_stock_remains_unknown_and_inputs_are_unchanged(self):
        sources = self.sources()
        sources["monthly_stock"].loc["001", pd.Timestamp("2025-01-01")] = np.nan
        before = deepcopy(sources)
        frame = self.frame(["2025-02-10"])
        old_frame = frame.copy(deep=True)
        result = enrich_features(frame, sources)
        self.assertTrue(np.isnan(result.iloc[0]["last_month_stock"]))
        pd.testing.assert_frame_equal(frame, old_frame)
        pd.testing.assert_frame_equal(sources["monthly_stock"], before["monthly_stock"])

    def test_current_snapshot_not_available_in_past_and_return_is_detached(self):
        sources = self.sources()
        self.assertEqual(current_profiles_at(sources, "2026-09-21"), {})
        current = current_profiles_at(sources, "2026-09-22")
        current["001"]["on_hand"] = 999
        self.assertEqual(sources["current"]["001"]["on_hand"], 50)

    def test_empty_frame_and_missing_origin_are_handled_explicitly(self):
        self.assertTrue(enrich_features(self.frame([]), self.sources()).empty)
        frame = self.frame(["2025-02-10"])
        frame["origin"] = pd.NaT
        with self.assertRaises(ValueError):
            enrich_features(frame, self.sources())


if __name__ == "__main__":
    unittest.main()
