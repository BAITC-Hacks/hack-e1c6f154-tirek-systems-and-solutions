from copy import deepcopy
from datetime import date
import json
import math
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from model.testbed.adapters import call_external, validate_request, validate_prediction
from model.testbed.baseline import forecast
from model.testbed.business import check_cases, reference_calculate, compare
from model.testbed.generator import generate, PROTOCOL, poisson, rng_for
from model.testbed.metrics import aggregate
from model.testbed.run import freeze, verify_manifest, DEFAULT_ADAPTER, DEFAULT_CALCULATOR, evaluate


class GenerationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = {s: generate(s, 101, PROTOCOL["origins"][0]) for s in PROTOCOL["scenarios"]}

    def test_all_scenarios_public_boundary(self):
        self.assertEqual(len(self.cases), 12)
        for case in self.cases.values():
            with self.subTest(scenario=case.scenario):
                validate_request(case.observed)
                self.assertEqual(len(case.observed["items"]), 4)
                for item in case.observed["items"]:
                    by_day = {}
                    for event in item["events"]:
                        by_day[event["date"]] = by_day.get(event["date"], 0) + event["quantity"]
                    for row in item["history"]:
                        self.assertEqual(row["observed_quantity"], by_day.get(row["date"], 0))

    def test_determinism_and_seed_variation(self):
        self.assertEqual(self.cases["stable"], generate("stable", 101, PROTOCOL["origins"][0]))
        self.assertNotEqual(self.cases["stable"].truth, generate("stable", 202, PROTOCOL["origins"][0]).truth)
        original_ids = {e["client_id"] for e in self.cases["stable"].observed["items"][0]["events"]}
        other_ids = {e["client_id"] for e in generate("stable", 202, PROTOCOL["origins"][0]).observed["items"][0]["events"]}
        self.assertFalse(original_ids & other_ids)

    def test_paired_regular_truth_and_projects_separate(self):
        base = self.cases["stable"]
        for scenario in ("single_project", "split_project", "full_stockout", "partial_stockout"):
            self.assertEqual(base.truth, self.cases[scenario].truth)
        for sku in base.truth:
            single = self.cases["single_project"].projects[sku]["one_off_quantity"]
            split = self.cases["split_project"].projects[sku]["one_off_quantity"]
            self.assertEqual(sum(single), sum(split))
            self.assertEqual(sum(v > 0 for v in split), 6)
            self.assertEqual(sum(v > 0 for v in single), 2)

    def test_availability_censors_and_never_creates_demand(self):
        for scenario in ("full_stockout", "partial_stockout"):
            case = self.cases[scenario]
            fractions = set()
            for item in case.observed["items"]:
                truth = case.truth[item["sku"]]["regular_realized"]
                for i, row in enumerate(item["history"]):
                    fractions.add(row["availability_fraction"])
                    self.assertLessEqual(row["observed_quantity"], truth[i])
                    if row["availability_fraction"] == 0:
                        self.assertEqual(row["observed_quantity"], 0)
            self.assertIn(0, fractions)
            if scenario == "partial_stockout":
                self.assertIn(.25, fractions)

    def test_new_product_has_only_post_launch_history(self):
        for index, item in enumerate(self.cases["new_product"].observed["items"]):
            self.assertEqual(len(item["history"]), 0 if index % 2 == 0 else 8)
            self.assertTrue(item["analogue_history"])
            self.assertTrue(all(r["date"] >= item["launch_date"] for r in item["history"]))

    def test_unannounced_shock_not_in_public_context(self):
        self.assertTrue(all(not i["known_promotions"] for i in self.cases["demand_shock"].observed["items"]))
        for truth in self.cases["demand_shock"].truth.values():
            self.assertGreater(sum(truth["regular_expectation"][-28:]) / 28,
                               sum(truth["regular_expectation"][-56:-28]) / 28 * 2)

    def test_private_future_mutation_cannot_affect_input_or_forecast(self):
        case = deepcopy(self.cases["stable"])
        before = json.dumps(case.observed, sort_keys=True)
        expected = forecast(case.observed)
        for truth in case.truth.values():
            truth["regular_realized"][-28:] = [1000000] * 28
        case.projects.clear()
        self.assertEqual(before, json.dumps(case.observed, sort_keys=True))
        self.assertEqual(expected, forecast(case.observed))

    def test_generator_poisson_expectation_sanity_development_only(self):
        rng = rng_for(101, "moment-check")
        values = [poisson(rng, 70) for _ in range(4000)]
        self.assertLess(abs(sum(values) / len(values) - 70), 1)


class BoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.request = generate("stable", 101, PROTOCOL["origins"][0]).observed
        cls.request["items"][0]["analogue_history"] = [{"date": cls.request["as_of"], "quantity": 10, "source": "synthetic-test"}]

    def test_no_hidden_top_level_or_nested_fields(self):
        for field in ("seed", "scenario", "truth", "generator_parameters"):
            request = deepcopy(self.request)
            request[field] = 123
            with self.assertRaises(ValueError):
                validate_request(request)
        request = deepcopy(self.request)
        request["items"][0]["history"][0]["latent"] = 42
        with self.assertRaises(ValueError):
            validate_request(request)

    def test_future_observations_and_unannounced_promotion_rejected(self):
        for name in ("history", "events", "analogue_history"):
            request = deepcopy(self.request)
            request["items"][0][name][0]["date"] = "2099-01-01"
            with self.assertRaises(ValueError):
                validate_request(request)
        request = generate("known_promotion", 101, PROTOCOL["origins"][0]).observed
        request["items"][0]["known_promotions"][0]["announced_at"] = "2099-01-01"
        with self.assertRaises(ValueError):
            validate_request(request)

    def test_duplicate_import_identifiers_rejected(self):
        for name in ("history", "events"):
            request = deepcopy(self.request)
            request["items"][0][name].append(deepcopy(request["items"][0][name][0]))
            with self.assertRaises(ValueError):
                validate_request(request)

    def test_prediction_exact_coverage_length_and_finiteness(self):
        good = forecast(self.request)
        for bad in ({}, {**good, "UNKNOWN": [0] * 28}, {**good, "SKU-001": [0] * 27}):
            with self.assertRaises(ValueError):
                validate_prediction(self.request, bad)
        for value in (-1, float("nan"), float("inf"), True, "1"):
            bad = deepcopy(good)
            bad["SKU-001"][0] = value
            with self.assertRaises(ValueError):
                validate_prediction(self.request, bad)

    def test_process_transport_and_mutation_isolation(self):
        before = deepcopy(self.request)
        actual = call_external("model.testbed.tests.adapter_examples:mutating", self.request)
        self.assertEqual(actual["SKU-001"], [1.0] * 28)
        self.assertEqual(before, self.request)

    def test_process_crash_missing_output_timeout(self):
        for name, error in (("missing_sku", ValueError), ("crashing", RuntimeError), ("sleeping", subprocess.TimeoutExpired)):
            with self.assertRaises(error):
                call_external(f"model.testbed.tests.adapter_examples:{name}", self.request, timeout=.2)


class BaselineAndBusinessTests(unittest.TestCase):
    def test_business_fixtures(self):
        for result in check_cases(reference_calculate):
            with self.subTest(case=result["id"]):
                self.assertTrue(result["passed"], result["failures"])

    def test_checker_detects_wrong_quantity_null_and_nonfinite(self):
        def broken(request):
            result = reference_calculate(request)
            result["items"][0]["quantity"] = 0
            return result
        self.assertTrue(any(not r["passed"] for r in check_cases(broken)))
        for value in (None, True, float("nan")):
            self.assertTrue(compare({"q": 1}, {"q": value}))

    def test_stockout_correction_exceeds_raw_sales_forecast(self):
        for scenario in ("full_stockout", "partial_stockout"):
            request = generate(scenario, 101, PROTOCOL["origins"][0]).observed
            result = forecast(request)
            for item in request["items"]:
                raw = sum(r["observed_quantity"] for r in item["history"][-56:]) / 2
                self.assertGreater(sum(result[item["sku"]]), raw)

    def test_regular_large_customer_not_excluded(self):
        request = generate("recurring_client", 101, PROTOCOL["origins"][0]).observed
        result = forecast(request)
        for item in request["items"]:
            raw = sum(r["observed_quantity"] for r in item["history"][-56:]) / 2
            self.assertAlmostEqual(sum(result[item["sku"]]), raw)

    def test_incomplete_day_not_treated_as_zero_or_complete_sales(self):
        request = generate("stable", 101, PROTOCOL["origins"][0]).observed
        item = request["items"][0]
        item["history"] = item["history"][-28:]
        item["events"] = []
        for row in item["history"]:
            row["observed_quantity"] = 10
        item["history"][-1].update(complete=False, observed_quantity=1000)
        self.assertAlmostEqual(sum(forecast(request)[item["sku"]]), 280)

    def test_one_client_split_across_days(self):
        request = generate("split_project", 101, PROTOCOL["origins"][0]).observed
        for item in request["items"]:
            clients = {e["client_id"] for e in item["events"]}
            events = next([e for e in item["events"] if e["client_id"] == client]
                          for client in clients if len([e for e in item["events"] if e["client_id"] == client]) == 15)
            self.assertEqual(len({e["date"] for e in events}), 3)
            self.assertEqual(len(events), 15)
        clean = forecast(generate("stable", 101, PROTOCOL["origins"][0]).observed)
        for sku, values in forecast(request).items():
            self.assertAlmostEqual(sum(values), sum(clean[sku]))


class MetricAndFreezeTests(unittest.TestCase):
    def row(self, prediction, actual, expected=None, error=None):
        return {"forecast_quantity": prediction, "realized_regular_quantity": actual,
                "expected_regular_quantity": actual if expected is None else expected, "project_quantity": 100000,
                "daily_absolute_error": 0, "daily_absolute_error_expectation": 0, "error": error}

    def test_wape_weighting_zero_rows_and_separate_expectation(self):
        result = aggregate([self.row(80,100,80), self.row(30,0,30)])
        self.assertEqual(result["wape_realized"], .5)
        self.assertEqual(result["wape_expectation"], 0)
        self.assertEqual(result["zero_realized_rows"], 1)
        self.assertIsNone(aggregate([self.row(10,0)])["wape_realized"])

    def test_failed_rows_make_complete_metric_unavailable(self):
        result = aggregate([self.row(100,100), self.row(None,200,error="failed")])
        self.assertEqual(result["rows"], 2)
        self.assertEqual(result["realized_regular_quantity"], 300)
        self.assertIsNone(result["wape_realized"])
        self.assertEqual(result["failed_rows"], 1)

    def test_freeze_refuses_changed_source_and_overwrite(self):
        from argparse import Namespace
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "freeze.json"
            args = Namespace(manifest=str(path), adapter=DEFAULT_ADAPTER, calculator=DEFAULT_CALCULATOR, artifact=[])
            freeze(args)
            verify_manifest(path, DEFAULT_ADAPTER, DEFAULT_CALCULATOR)
            with self.assertRaises(ValueError):
                freeze(args)
            with patch("model.testbed.run.file_hashes", return_value={}):
                with self.assertRaises(ValueError):
                    verify_manifest(path, DEFAULT_ADAPTER, DEFAULT_CALCULATOR)

    def test_seed_sets_disjoint_and_fixed(self):
        self.assertEqual(PROTOCOL["development_seeds"], [101,202,303])
        self.assertEqual(PROTOCOL["final_seeds"], [909,1337,2027,4099])
        self.assertFalse(set(PROTOCOL["development_seeds"]) & set(PROTOCOL["final_seeds"]))

    def test_failed_adapter_stays_in_report(self):
        from argparse import Namespace
        # One small development-only run; no final seeds are generated by unit tests.
        with tempfile.TemporaryDirectory() as folder, patch.dict(PROTOCOL, {"development_seeds":[101], "scenarios":["stable","single_project","split_project"], "origins":["2025-07-01"]}):
            args = Namespace(split="development",adapter="model.testbed.tests.adapter_examples:missing_sku",calculator=DEFAULT_CALCULATOR,
                             timeout=2,output=folder,require_target=False)
            self.assertEqual(evaluate(args), 2)
            report = json.loads((Path(folder)/"report.json").read_text())
            self.assertEqual(report["overall"]["rows"],12)
            self.assertEqual(report["overall"]["failed_rows"],12)
            self.assertIsNone(report["overall"]["wape_realized"])


if __name__ == "__main__":
    unittest.main()
