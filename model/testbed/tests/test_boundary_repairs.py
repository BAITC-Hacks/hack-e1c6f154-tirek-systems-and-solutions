"""Regression probes for strict public inputs and numeric failure retention."""
from copy import deepcopy
from datetime import date
import json
import math
import subprocess
import unittest
from unittest.mock import patch

from model.testbed.adapters import call_external, validate_prediction, validate_request
from model.testbed.metrics import aggregate, checked_sum


def public_request():
    return {
        "schema_version": "forecast-input-v2", "as_of": "2025-07-01", "horizon_days": 2,
        "items": [{
            "sku": "SKU-A", "unit": "шт", "warehouse_id": "warehouse-A",
            "supplier_id": "supplier-A", "category_raw": "C1", "launch_date": "2025-06-01",
            "history": [
                {"date": "2025-06-30", "observed_quantity": 5, "availability_fraction": 1, "complete": True},
                {"date": "2025-07-01", "observed_quantity": 3, "availability_fraction": .5, "complete": False},
            ],
            "events": [
                {"event_id": "document-A", "date": "2025-06-30", "client_id": "client-A", "quantity": 5},
                {"event_id": "document-B", "date": "2025-07-01", "client_id": "client-A", "quantity": 3},
            ],
            "known_promotions": [{"start_date": "2025-07-02", "end_date": "2025-07-03",
                                  "announced_at": "2025-06-28", "planned_multiplier": 1.8, "source": "plan-A"}],
            "analogue_history": [{"date": "2025-05-01", "quantity": 10, "source": "analogue-A"}],
        }],
    }


class PublicContractRepairs(unittest.TestCase):
    def test_valid_packet_and_reordered_records_are_accepted_without_mutation(self):
        request = public_request()
        request["items"][0]["history"].reverse()
        request["items"][0]["events"].reverse()
        original = deepcopy(request)
        validate_request(request)
        self.assertEqual(request, original)

    def test_missing_calendar_days_and_empty_new_product_history_are_supported(self):
        request = public_request()
        item = request["items"][0]
        item["history"][0]["date"] = item["events"][0]["date"] = "2025-06-20"
        validate_request(request)
        item["history"] = []
        item["events"] = []
        item["launch_date"] = request["as_of"]
        validate_request(request)

    def test_horizon_rejects_bool_float_zero_negative_and_calendar_overflow(self):
        for horizon in (True, False, 0, -1, 2.0, "2", None, 10**100):
            with self.subTest(horizon=horizon):
                request = public_request()
                request["horizon_days"] = horizon
                with self.assertRaises(ValueError):
                    validate_request(request)

    def test_every_date_is_strict_iso_and_ordered_in_time(self):
        for value in ("20250701", "2025-W27-2", "2025-02-30", "2025-7-1", "2025-07-01T00:00:00", date(2025, 7, 1), None):
            for location in ("as_of", "launch_date", "history", "events", "analogue_history", "start_date", "end_date", "announced_at"):
                with self.subTest(value=value, location=location):
                    request = public_request()
                    item = request["items"][0]
                    if location == "as_of":
                        request[location] = value
                    elif location == "launch_date":
                        item[location] = value
                    elif location in ("history", "events", "analogue_history"):
                        item[location][0]["date"] = value
                    else:
                        item["known_promotions"][0][location] = value
                    with self.assertRaises(ValueError):
                        validate_request(request)
        for field, value in (("start_date", "2025-07-04"), ("announced_at", "2025-07-02")):
            request = public_request()
            request["items"][0]["known_promotions"][0][field] = value
            with self.assertRaises(ValueError):
                validate_request(request)
        for collection, value in (("history", "2025-05-31"), ("events", "2025-05-31"), ("analogue_history", "2025-07-02")):
            request = public_request()
            request["items"][0][collection][0]["date"] = value
            with self.assertRaises(ValueError):
                validate_request(request)

    def test_promotion_multiplier_is_strictly_positive_and_finite(self):
        for value in (0, -1, True, "2", float("inf"), float("nan"), 10**400):
            with self.subTest(value=repr(value)):
                request = public_request()
                request["items"][0]["known_promotions"][0]["planned_multiplier"] = value
                with self.assertRaises(ValueError):
                    validate_request(request)

    def test_complete_is_boolean_and_availability_is_bounded(self):
        for value in ("false", 0, 1, None, []):
            request = public_request()
            request["items"][0]["history"][0]["complete"] = value
            with self.assertRaises(ValueError):
                validate_request(request)
        for value in (-.1, 1.1, True, float("nan"), float("inf")):
            request = public_request()
            request["items"][0]["history"][0]["availability_fraction"] = value
            with self.assertRaises(ValueError):
                validate_request(request)
        request = public_request()
        request["items"][0]["history"][0]["availability_fraction"] = 0
        with self.assertRaises(ValueError):
            validate_request(request)

    def test_quantities_are_nonnegative_finite_json_numbers(self):
        for collection, field in (("history", "observed_quantity"), ("events", "quantity"), ("analogue_history", "quantity")):
            for value in (-1, True, None, "1", float("nan"), float("inf"), 10**400):
                with self.subTest(collection=collection, value=repr(value)):
                    request = public_request()
                    request["items"][0][collection][0][field] = value
                    with self.assertRaises(ValueError):
                        validate_request(request)

    def test_identity_and_collection_types_are_not_silently_coerced(self):
        for field in ("sku", "unit", "warehouse_id", "supplier_id", "category_raw"):
            for value in (None, "", " ", " leading", 123, []):
                request = public_request()
                request["items"][0][field] = value
                with self.assertRaises(ValueError):
                    validate_request(request)
        for field in ("event_id", "client_id"):
            request = public_request()
            request["items"][0]["events"][0][field] = None
            with self.assertRaises(ValueError):
                validate_request(request)
        for collection in ("history", "events", "analogue_history", "known_promotions"):
            for value in (None, {}, (), [None]):
                request = public_request()
                request["items"][0][collection] = value
                with self.assertRaises(ValueError):
                    validate_request(request)
        for value in (None, [], "request", {}):
            with self.assertRaises(ValueError):
                validate_request(value)

    def test_duplicate_identities_are_rejected(self):
        request = public_request()
        request["items"].append(deepcopy(request["items"][0]))
        with self.assertRaises(ValueError):
            validate_request(request)
        for collection in ("history", "events", "analogue_history", "known_promotions"):
            request = public_request()
            records = request["items"][0][collection]
            records.append(deepcopy(records[0]))
            with self.assertRaisesRegex(ValueError, "Duplicate"):
                validate_request(request)

    def test_event_date_coverage_and_daily_quantity_must_match(self):
        request = public_request()
        request["items"][0]["events"][0]["date"] = "2025-06-29"
        with self.assertRaisesRegex(ValueError, "matching history"):
            validate_request(request)
        for mutation in ("wrong_quantity", "missing_documents", "overflow"):
            request = public_request()
            item = request["items"][0]
            if mutation == "wrong_quantity":
                item["events"][0]["quantity"] = 4
            elif mutation == "missing_documents":
                item["events"] = []
            else:
                item["history"][0]["observed_quantity"] = 1e308
                item["events"][0]["quantity"] = 1e308
                item["events"].append(dict(item["events"][0], event_id="document-overflow"))
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                validate_request(request)

    def test_horizon_overflow_is_rejected_at_external_prediction_boundary(self):
        request = public_request()
        for values in ([1e308, 1e308], [10**400, 0]):
            with self.assertRaises(ValueError):
                validate_prediction(request, {"SKU-A": values})
        self.assertEqual(validate_prediction(request, {"SKU-A": [1e308, 0]}), {"SKU-A": [1e308, 0]})
        response = subprocess.CompletedProcess([], 0, stdout='{"SKU-A":[1e308,1e308]}', stderr="")
        with patch("model.testbed.adapters.subprocess.run", return_value=response):
            with self.assertRaisesRegex(ValueError, "forecast horizon"):
                call_external("unused:forecast", request)

    def test_duplicate_json_sku_is_not_discarded_by_transport(self):
        response = subprocess.CompletedProcess([], 0, stdout='{"SKU-A":[1,2],"SKU-A":[3,4]}', stderr="")
        with patch("model.testbed.adapters.subprocess.run", return_value=response):
            with self.assertRaisesRegex(ValueError, "Duplicate JSON"):
                call_external("unused:forecast", public_request())


class NumericAggregationRepairs(unittest.TestCase):
    @staticmethod
    def row(forecast=1, realized=1, expected=1, daily=0, project=0, error=None):
        return {"forecast_quantity": forecast, "realized_regular_quantity": realized,
                "expected_regular_quantity": expected, "daily_absolute_error": daily,
                "daily_absolute_error_expectation": daily, "project_quantity": project, "error": error}

    def assert_json_safe_failure(self, rows, label):
        actual = aggregate(rows)
        json.dumps(actual, allow_nan=False)
        self.assertEqual(actual["rows"], len(rows))
        self.assertTrue(actual["numerical_failure"])
        self.assertTrue(any(label in message for message in actual["numerical_errors"]), actual["numerical_errors"])
        for key in ("wape_realized", "wape_expectation", "bias_realized", "bias_expectation", "daily_wape_realized", "daily_wape_expectation"):
            self.assertIsNone(actual[key])
        return actual

    def test_checked_sum_rejects_overflow_nonfinite_and_non_json_numbers(self):
        self.assertEqual(checked_sum([1, -.5, .5]), 1)
        for values in ([1e308, 1e308], [math.inf], [math.nan], [10**400], [True], [None], ["1"]):
            with self.subTest(values=repr(values)), self.assertRaises(ValueError):
                checked_sum(values, "probe")

    def test_denominator_overflow_preserves_other_representable_volumes(self):
        rows = [self.row(realized=1e308, project=3), self.row(realized=1e308, project=4)]
        actual = self.assert_json_safe_failure(rows, "realized_regular_quantity")
        self.assertIsNone(actual["realized_regular_quantity"])
        self.assertEqual(actual["expected_regular_quantity"], 2)
        self.assertEqual(actual["project_quantity"], 7)
        self.assertEqual(actual["failed_rows"], 0)

    def test_sum_of_individually_finite_forecast_errors_cannot_break_report(self):
        rows = [self.row(forecast=1e308), self.row(forecast=1e308)]
        actual = self.assert_json_safe_failure(rows, "absolute_error_realized")
        self.assertIsNone(actual["absolute_error_realized"])
        self.assertEqual(actual["realized_regular_quantity"], 2)

    def test_daily_error_and_project_totals_have_overflow_guards(self):
        for field, label in (("daily", "daily_absolute_error"), ("project", "project_quantity")):
            rows = [self.row(**{field: 1e308}), self.row(**{field: 1e308})]
            actual = self.assert_json_safe_failure(rows, label)
            self.assertEqual(actual["realized_regular_quantity"], 2)
            self.assertEqual(actual["absolute_error_realized"], 0)

    def test_ratio_overflow_is_a_failure_even_when_all_sums_are_finite(self):
        actual = self.assert_json_safe_failure([self.row(forecast=1e308, realized=1e-308, expected=1e-308, daily=1e308)], "wape_realized")
        self.assertEqual(actual["absolute_error_realized"], 1e308)
        self.assertEqual(actual["realized_regular_quantity"], 1e-308)

    def test_invalid_numeric_row_values_become_serializable_failures(self):
        for field in ("forecast_quantity", "realized_regular_quantity", "expected_regular_quantity", "project_quantity", "daily_absolute_error", "daily_absolute_error_expectation"):
            for value in (math.nan, math.inf, 10**400, -1, True, None):
                with self.subTest(field=field, value=repr(value)):
                    row = self.row()
                    row[field] = value
                    actual = aggregate([row])
                    self.assertTrue(actual["numerical_failure"])
                    json.dumps(actual, allow_nan=False)

    def test_normal_weighting_zero_denominators_and_failed_rows_still_work(self):
        actual = aggregate([self.row(80, 100, 80), self.row(30, 0, 30)])
        self.assertAlmostEqual(actual["wape_realized"], .5)
        self.assertEqual(actual["wape_expectation"], 0)
        self.assertEqual(actual["zero_realized_rows"], 1)
        self.assertFalse(actual["numerical_failure"])
        self.assertEqual(actual["numerical_errors"], [])
        zero = aggregate([self.row(10, 0, 0)])
        self.assertIsNone(zero["wape_realized"])
        self.assertEqual(zero["absolute_error_realized"], 10)
        self.assertFalse(zero["numerical_failure"])
        failed = aggregate([self.row(), self.row(None, 100, 100, daily=None, error="adapter failed")])
        self.assertEqual(failed["failed_rows"], 1)
        self.assertEqual(failed["realized_regular_quantity"], 101)
        self.assertIsNone(failed["wape_realized"])
        self.assertFalse(failed["numerical_failure"])


if __name__ == "__main__":
    unittest.main()
