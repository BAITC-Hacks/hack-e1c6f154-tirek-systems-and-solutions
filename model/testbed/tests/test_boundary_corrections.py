"""Independent boundary/metric regressions; no historical reports are rewritten."""
from argparse import Namespace
from copy import deepcopy
from decimal import Decimal
import contextlib
import io
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from model.testbed.adapters import validate_request, validate_prediction
from model.testbed.generator import PROTOCOL, generate
from model.testbed.metrics import aggregate
from model.testbed import run


class RequestCorrectionsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.request = generate("known_promotion", 101, PROTOCOL["origins"][0]).observed

    def reject(self, modify):
        request = deepcopy(self.request)
        modify(request)
        with self.assertRaises(ValueError):
            validate_request(request)

    def test_horizon_integer_positive_bounded(self):
        for value in (True, False, 28.0, "28", None, 0, -1, 367, 10**1000):
            with self.subTest(value=str(value)[:30]):
                self.reject(lambda r: r.update(horizon_days=value))
        for value in (1, 28, 366):
            request = deepcopy(self.request)
            request["horizon_days"] = value
            validate_request(request)
        self.reject(lambda r: r.update(as_of="9999-12-31"))

    def test_complete_is_boolean(self):
        for value in (0, 1, "false", None, [], {}):
            with self.subTest(value=value):
                self.reject(lambda r: r["items"][0]["history"][0].update(complete=value))
        request = deepcopy(self.request)
        request["items"][0]["history"][0]["complete"] = False
        validate_request(request)

    def test_canonical_dates_and_chronology(self):
        for value in ("20250701", "2025-W27-2", "2025-02-30", "2025-07-01T00:00:00", None):
            with self.subTest(value=value):
                self.reject(lambda r: r.update(as_of=value))
        self.reject(lambda r: r["items"][0]["history"].reverse())
        self.reject(lambda r: r["items"][0]["history"].append(deepcopy(r["items"][0]["history"][-1])))
        self.reject(lambda r: r["items"][0]["history"][0].update(date="1900-01-01"))
        self.reject(lambda r: r["items"][0]["events"][0].update(date="1900-01-01"))
        self.reject(lambda r: r["items"][0]["history"][-1].update(date="2099-01-01"))

    def test_promotions_are_known_positive_and_chronological(self):
        for key, value in (("planned_multiplier", 0), ("planned_multiplier", -1),
                           ("planned_multiplier", True), ("planned_multiplier", float("inf")),
                           ("start_date", "not-a-date"), ("end_date", "1900-01-01"),
                           ("announced_at", "2099-01-01"), ("announced_at", "2025-06-30")):
            with self.subTest(key=key, value=value):
                self.reject(lambda r: r["items"][0]["known_promotions"][0].update({key: value}))
        # Future promotion dates are legitimate when announced by cutoff.
        validate_request(self.request)

    def test_structural_and_numeric_errors_are_value_errors(self):
        for bad in (None, [], "request"):
            with self.assertRaises(ValueError):
                validate_request(bad)
        for modify in (lambda r: r.update(items=[]), lambda r: r.update(items={}),
                       lambda r: r["items"].__setitem__(0, []),
                       lambda r: r["items"][0].update(history={}),
                       lambda r: r["items"][0].update(sku=[]),
                       lambda r: r["items"][0]["history"][0].update(observed_quantity=10**1000)):
            self.reject(modify)


def metric_row(prediction, actual, expected=None, daily=0, error=None, unit="шт"):
    return {"forecast_quantity": prediction, "realized_regular_quantity": actual,
            "expected_regular_quantity": actual if expected is None else expected,
            "project_quantity": 99999, "daily_absolute_error": daily,
            "daily_absolute_error_expectation": daily, "error": error, "unit": unit}


class MetricCorrectionsTests(unittest.TestCase):
    def test_independent_decimal_formulas_and_zero_targets(self):
        rows = [metric_row(80, 100, 80, daily=80), metric_row(30, 0, 30, daily=30)]
        actual = aggregate(rows)
        prediction = list(map(Decimal, [80, 30]))
        target = list(map(Decimal, [100, 0]))
        absolute = sum(abs(p-y) for p, y in zip(prediction, target))
        signed = sum(p-y for p, y in zip(prediction, target))
        denominator = sum(target)
        self.assertEqual(actual["wape_realized"], float(absolute/denominator))
        self.assertEqual(actual["mae_realized"], float(absolute/len(rows)))
        self.assertEqual(actual["bias_realized"], float(signed/denominator))
        self.assertEqual(actual["daily_mae_realized"], float(Decimal(110)/Decimal(56)))
        self.assertEqual(actual["mae_expectation"], 0)
        self.assertEqual(actual["zero_realized_rows"], 1)
        self.assertIsNone(actual["aggregation_error"])

    def test_zero_denominator_retains_error_and_mae(self):
        actual = aggregate([metric_row(10, 0)])
        self.assertIsNone(actual["wape_realized"])
        self.assertIsNone(actual["bias_realized"])
        self.assertEqual(actual["absolute_error_realized"], 10)
        self.assertEqual(actual["mae_realized"], 10)
        self.assertIsNone(aggregate([])["mae_realized"])
        self.assertEqual(aggregate([])["absolute_error_realized"], 0)

    def test_failed_rows_keep_denominator_no_partial_accuracy(self):
        actual = aggregate([metric_row(100, 100), metric_row(None, 200, error="failed")])
        self.assertEqual(actual["realized_regular_quantity"], 300)
        self.assertEqual(actual["failed_rows"], 1)
        for key in ("wape_realized", "mae_realized", "bias_realized", "daily_mae_realized", "mae_expectation"):
            self.assertIsNone(actual[key], key)

    def test_mixed_units_are_not_pooled(self):
        rows = [metric_row(80, 100), metric_row(30, 20, unit="м")]
        combined = aggregate(rows)
        self.assertIsNone(combined["mae_realized"])
        self.assertIsNone(combined["realized_regular_quantity"])
        self.assertIn("Mixed units", combined["aggregation_error"])
        self.assertEqual(aggregate([rows[0]])["wape_realized"], .2)
        self.assertEqual(aggregate([rows[1]])["wape_realized"], .5)

    def test_prediction_horizon_and_aggregate_overflow(self):
        request = {"items": [{"sku": "A"}], "horizon_days": 28}
        with self.assertRaises(ValueError):
            validate_prediction(request, {"A": [1e308]*28})
        with self.assertRaises(ValueError):
            validate_prediction(request, {"A": [10**1000]*28})
        validate_prediction(request, {"A": [1e306]*28})
        result = aggregate([metric_row(2.8e307, 1, daily=2.8e307) for _ in range(12)])
        self.assertEqual(result["failed_rows"], 12)
        self.assertEqual(result["realized_regular_quantity"], 12)
        self.assertIn("Numeric aggregation failure", result["aggregation_error"])
        self.assertIsNone(result["wape_realized"])
        json.dumps(result, allow_nan=False)

    def evaluate_prediction(self, prediction):
        small = {"development_seeds": [101], "scenarios": ["stable", "single_project", "split_project"],
                 "origins": ["2025-07-01"]}
        def external(spec, request, timeout):
            return prediction(request)
        with tempfile.TemporaryDirectory() as folder, patch.dict(PROTOCOL, small), \
             patch.object(run, "call_external", side_effect=external), patch.object(run, "check_cases", return_value=[]), \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            args = Namespace(split="development", adapter=run.DEFAULT_ADAPTER, calculator=run.DEFAULT_CALCULATOR,
                             timeout=2, output=folder, require_target=False)
            code = run.evaluate(args)
            report = json.loads((Path(folder)/"report.json").read_text())
            self.assertTrue((Path(folder)/"rows.csv").exists())
            self.assertTrue((Path(folder)/"report.md").exists())
            json.dumps(report, allow_nan=False)
            return code, report

    def test_full_evaluate_retains_both_overflow_paths(self):
        for value, aggregation_failure in ((1e308, False), (1e306, True)):
            with self.subTest(value=value):
                code, report = self.evaluate_prediction(lambda r: {i["sku"]: [value]*28 for i in r["items"]})
                self.assertEqual(code, 2)
                self.assertEqual(report["overall"]["rows"], 12)
                self.assertEqual(report["overall"]["failed_rows"], 12)
                self.assertGreater(report["overall"]["realized_regular_quantity"], 0)
                self.assertIsNone(report["overall"]["wape_realized"])
                self.assertIsNone(report["overall"]["mae_realized"])
                self.assertFalse(report["forecast_target_met"])
                self.assertEqual(bool(report["aggregation_failures"]), aggregation_failure)
                self.assertTrue(all(row["error"] and row["forecast_quantity"] is None for row in report["rows"]))
                for group in report["groups"]["scenario_seed"].values():
                    self.assertIsNone(group["mae_realized"])

    def test_full_evaluate_saves_mae_in_all_groups(self):
        code, report = self.evaluate_prediction(lambda r: {i["sku"]: [1.0]*28 for i in r["items"]})
        self.assertEqual(code, 0)
        for metrics in [report["overall"]] + [g for groups in report["groups"].values() for g in groups.values()]:
            self.assertAlmostEqual(metrics["mae_realized"], metrics["absolute_error_realized"]/metrics["rows"])
            self.assertTrue(math.isfinite(metrics["mae_expectation"]))


if __name__ == "__main__":
    unittest.main()
