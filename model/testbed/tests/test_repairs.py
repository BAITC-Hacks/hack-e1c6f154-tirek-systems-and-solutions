"""Regressions for the audited data leak, temporal order and runner failure paths."""
from argparse import Namespace
from copy import deepcopy
import json
from pathlib import Path
import random
import tempfile
import unittest
from unittest.mock import patch

from model.testbed.baseline import forecast
from model.testbed.generator import generate, PROTOCOL
from model.testbed.run import DEFAULT_ADAPTER, DEFAULT_CALCULATOR, evaluate


class GeneratorRepairTests(unittest.TestCase):
    def test_identifiers_have_one_opaque_schema_for_all_purchase_classes(self):
        for seed in PROTOCOL["development_seeds"]:
            for scenario in ("stable", "split_project", "recurring_client", "combined"):
                request = generate(scenario, seed, PROTOCOL["origins"][0]).observed
                ids = [event["event_id"] for item in request["items"] for event in item["events"]]
                self.assertEqual(len(ids), len(set(ids)))
                for value in ids:
                    self.assertRegex(value, r"^evt_[0-9a-f]{32}$")
                renamed = deepcopy(request)
                for item in renamed["items"]:
                    for index, event in enumerate(item["events"]):
                        event["event_id"] = f"independent-document-{index}"
                self.assertEqual(forecast(request), forecast(renamed))

    def test_record_permutation_does_not_change_recent_window(self):
        request = generate("seasonal_growth", 101, PROTOCOL["origins"][0]).observed
        expected = forecast(request)
        shuffled = deepcopy(request)
        rng = random.Random(101)
        for item in shuffled["items"]:
            rng.shuffle(item["history"])
            rng.shuffle(item["events"])
        self.assertEqual(expected, forecast(shuffled))

    def test_sparse_history_uses_calendar_window_not_last_56_records(self):
        request = generate("stable", 101, PROTOCOL["origins"][0]).observed
        for item in request["items"]:
            item["events"] = []
            old = item["history"][:100]
            recent = item["history"][-2:]
            for row in old:
                row["observed_quantity"] = 1000
            for row in recent:
                row["observed_quantity"] = 10
            item["history"] = old + recent
        self.assertTrue(all(abs(sum(values) - 280) < 1e-9 for values in forecast(request).values()))

    def test_no_exposure_is_unknown_not_zero_demand(self):
        request = generate("stable", 101, PROTOCOL["origins"][0]).observed
        for item in request["items"]:
            for row in item["history"]:
                row.update(observed_quantity=0, availability_fraction=0)
            item["events"] = []
        with self.assertRaisesRegex(ValueError, "demand is unknown"):
            forecast(request)
        # Observed zero with complete availability is genuinely a zero observation.
        for item in request["items"]:
            for row in item["history"]:
                row["availability_fraction"] = 1
        self.assertTrue(all(sum(values) == 0 for values in forecast(request).values()))


class EvaluationRepairTests(unittest.TestCase):
    def test_exposed_final_seeds_cannot_be_claimed_as_new_holdout(self):
        args = Namespace(split="final", output="unused")
        with patch("model.testbed.run.generate") as generating:
            with self.assertRaisesRegex(ValueError, "already exposed"):
                evaluate(args)
            generating.assert_not_called()

    def test_historical_reports_cannot_be_overwritten(self):
        root = Path(__file__).resolve().parents[1]
        args = Namespace(split="development", output=str(root / "reports/final"))
        with self.assertRaisesRegex(ValueError, "immutable"):
            evaluate(args)

    def run_numeric_failure(self, value, label):
        with tempfile.TemporaryDirectory() as folder:
            args = Namespace(split="development", output=folder, timeout=1, require_target=False,
                             adapter=DEFAULT_ADAPTER, calculator=DEFAULT_CALCULATOR)
            def overflowing(spec, request, *args, **kwargs):
                return {item["sku"]: [value] * request["horizon_days"] for item in request["items"]}
            with patch.dict(PROTOCOL, {"development_seeds": [101], "origins": ["2025-07-01"],
                                     "scenarios": ["stable", "single_project", "split_project"]}), \
                 patch("model.testbed.run.call_external", side_effect=overflowing), \
                 patch("model.testbed.run.check_cases", return_value=[]):
                self.assertEqual(evaluate(args), 2, label)
            report = json.loads((Path(folder) / "report.json").read_text())
            self.assertEqual(len(report["rows"]), 12)
            self.assertIsNone(report["overall"]["wape_realized"])
            self.assertFalse(report["forecast_target_met"])
            self.assertTrue((Path(folder) / "rows.csv").exists())
            return report

    def test_single_horizon_overflow_retains_rows_and_report(self):
        report = self.run_numeric_failure(1e308, "individual horizon overflow")
        self.assertEqual(report["overall"]["failed_rows"], 12)
        self.assertTrue(all(row["error"] for row in report["rows"]))

    def test_aggregate_overflow_retains_finite_rows_and_report(self):
        report = self.run_numeric_failure(1e306, "sum of finite horizons overflow")
        # Every 28-day horizon is finite; the sum over 12 horizons overflows.
        self.assertTrue(report["overall"]["numerical_failure"])


if __name__ == "__main__":
    unittest.main()
