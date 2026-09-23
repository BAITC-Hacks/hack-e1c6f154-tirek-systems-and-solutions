"""Observed-only tests; no generated hidden truth is used by the predictor."""
from copy import deepcopy
from datetime import date, timedelta
import unittest

import numpy as np

from model.regular_forecast.predictor import clean_history, fit_predict, forecast_with_audit


def request(days=420, rate=10):
    cutoff = date(2026, 4, 1)
    rows, events = [], []
    for offset in range(-days + 1, 1):
        stamp = (cutoff + timedelta(days=offset)).isoformat()
        rows.append({"date": stamp, "observed_quantity": rate, "availability_fraction": 1.0, "complete": True})
        events.append({"event_id": stamp, "date": stamp, "client_id": "returning", "quantity": rate})
    return {"schema_version": "forecast-input-v2", "as_of": cutoff.isoformat(), "horizon_days": 28,
            "items": [{"sku": "x", "unit": "шт", "warehouse_id": "test", "supplier_id": "s",
                       "category_raw": "c", "launch_date": rows[0]["date"], "history": rows,
                       "events": events, "known_promotions": [], "analogue_history": []}]}


class PredictorTests(unittest.TestCase):
    def test_constant_rate_and_exact_horizon(self):
        forecast, audit = forecast_with_audit(request())
        np.testing.assert_allclose(forecast["x"], np.full(28, 10), atol=1e-5)
        self.assertEqual(audit["x"]["validation_windows"], 3)

    def test_full_and_partial_availability_preserve_underlying_rate(self):
        for fraction in (0.0, 0.25, 0.5):
            req = request()
            changed = {r["date"] for r in req["items"][0]["history"][-42:]}
            for row in req["items"][0]["history"][-42:]:
                row["availability_fraction"] = fraction
                row["observed_quantity"] *= fraction
            for event in req["items"][0]["events"]:
                if event["date"] in changed:
                    event["quantity"] *= fraction
            predicted, _ = forecast_with_audit(req, "mean182")
            np.testing.assert_allclose(predicted["x"], 10, atol=1e-7)

    def test_split_project_does_not_change_regular_forecast(self):
        req = request()
        row = req["items"][0]["history"][-18]
        row["observed_quantity"] += 500
        for n in range(5):
            req["items"][0]["events"].append({"event_id": f"project-{n}", "date": row["date"],
                                              "client_id": "rare-client", "quantity": 100})
        predicted, audit = forecast_with_audit(req, "mean182")
        np.testing.assert_allclose(predicted["x"], 10, atol=1e-7)
        self.assertEqual(audit["x"]["excluded_client_windows"][0]["quantity"], 500)

    def test_repeated_large_client_is_not_deleted(self):
        req = request()
        for index in (14, 28, 42, 56, 70):
            row = req["items"][0]["history"][-index]
            row["observed_quantity"] += 200
            req["items"][0]["events"].append({"event_id": f"recurring-{index}", "date": row["date"],
                                              "client_id": "large-returning", "quantity": 200})
        _, quantities, _, excluded = clean_history(req["items"][0], req["as_of"])
        self.assertEqual(excluded, [])
        self.assertEqual(quantities.sum(), 4200 + 1000)

    def test_recurring_client_can_also_make_one_off_project(self):
        req = request()
        row = req["items"][0]["history"][-18]
        row["observed_quantity"] += 500
        req["items"][0]["events"].append({"event_id": "extra", "date": row["date"],
                                          "client_id": "returning", "quantity": 500})
        prediction, audit = forecast_with_audit(req, "mean182")
        np.testing.assert_allclose(prediction["x"], 10, atol=1e-7)
        self.assertEqual(sum(e["quantity"] for e in audit["x"]["excluded_client_windows"]), 500)

    def test_zero_quantity_events_cannot_make_one_off_client_recurring(self):
        req = request()
        item = req["items"][0]
        row = item["history"][-18]
        row["observed_quantity"] += 500
        item["events"].append({"event_id": "rare-project", "date": row["date"],
                                "client_id": "rare-client", "quantity": 500})
        before, audit_before = forecast_with_audit(req, "mean182")
        for index in (-80, -50):
            item["events"].append({"event_id": f"zero-{index}", "date": item["history"][index]["date"],
                                    "client_id": "rare-client", "quantity": 0})
        after, audit_after = forecast_with_audit(req, "mean182")
        np.testing.assert_allclose(before["x"], 10, atol=1e-7)
        np.testing.assert_array_equal(before["x"], after["x"])
        self.assertEqual(audit_before["x"]["excluded_client_windows"],
                         audit_after["x"]["excluded_client_windows"])

    def test_no_exposure_remains_unknown_regardless_of_history_length(self):
        for length in (27, 28, 60):
            req = request(length, rate=0)
            for row in req["items"][0]["history"]:
                row["availability_fraction"] = 0
            with self.assertRaises(ValueError):
                forecast_with_audit(req)
            req["items"][0]["analogue_history"] = [{"date": "2026-03-01", "quantity": 10, "source": "category"}]
            prediction, _ = forecast_with_audit(req)
            np.testing.assert_allclose(prediction["x"], 10, atol=1e-7)

    def test_client_windows_slide_across_bucket_boundaries(self):
        req = request()
        item = req["items"][0]
        for index, quantity in ((21, 1), (15, 50), (14, 50)):
            row = item["history"][-index]
            row["observed_quantity"] += quantity
            item["events"].append({"event_id": f"boundary-{index}", "date": row["date"],
                                    "client_id": "rare-project", "quantity": quantity})
        _, _, _, excluded = clean_history(item, req["as_of"])
        self.assertEqual(sum(w["quantity"] for w in excluded), 100)

    def test_incomplete_observation_cannot_change_training_or_selection(self):
        req = request()
        row = req["items"][0]["history"][-40]
        row["complete"] = False
        before, audit_before = forecast_with_audit(req)
        row["observed_quantity"] += 10000
        after, audit_after = forecast_with_audit(req)
        np.testing.assert_allclose(before["x"], after["x"], atol=1e-7)
        self.assertEqual(audit_before["x"]["method_losses"], audit_after["x"]["method_losses"])

    def test_known_future_promotion_changes_only_its_days(self):
        req = request()
        req["items"][0]["known_promotions"] = [{"start_date": "2026-04-03", "end_date": "2026-04-05",
                                                    "announced_at": "2026-03-01", "planned_multiplier": 2,
                                                    "source": "commercial-plan"}]
        prediction, _ = forecast_with_audit(req, "mean182")
        np.testing.assert_allclose(prediction["x"][:5], [10, 20, 20, 20, 10])
        req["items"][0]["known_promotions"][0]["announced_at"] = "2026-04-02"
        with self.assertRaises(ValueError):
            forecast_with_audit(req)

    def test_rejects_future_observations_hidden_fields_and_duplicate_events(self):
        req = request()
        for mutate in (lambda r: r.update(truth=[1000]),
                       lambda r: r["items"][0].update(hidden_truth=[100]),
                       lambda r: r["items"][0]["history"][-1].update(latent_demand=10),
                       lambda r: r["items"][0]["history"][-1].update(complete="false"),
                       lambda r: r["items"][0]["history"][-1].update(date="2026-04-02"),
                       lambda r: r["items"][0]["events"].append(r["items"][0]["events"][0])):
            altered = deepcopy(req)
            mutate(altered)
            with self.assertRaises(ValueError):
                forecast_with_audit(altered)

    def test_rejects_boolean_numbers_and_non_boolean_completeness(self):
        req = request()
        item = req["items"][0]
        item["known_promotions"] = [{"start_date": "2026-04-03", "end_date": "2026-04-05",
                                      "announced_at": "2026-03-01", "planned_multiplier": 2,
                                      "source": "commercial-plan"}]
        item["analogue_history"] = [{"date": "2026-03-01", "quantity": 10, "source": "category"}]
        cases = [("history", "observed_quantity", True), ("history", "availability_fraction", True),
                 ("events", "quantity", True), ("known_promotions", "planned_multiplier", True),
                 ("analogue_history", "quantity", True), ("history", "complete", 0),
                 ("history", "complete", 1)]
        for collection, field, value in cases:
            altered = deepcopy(req)
            altered["items"][0][collection][0][field] = value
            with self.subTest(collection=collection, field=field, value=value), self.assertRaises(ValueError):
                forecast_with_audit(altered, "mean182")

    def test_rejects_unused_future_analogue_and_invalid_promotion_dates(self):
        req = request()
        # The long, fully observed history would not need an analogue, but an
        # invalid future observation must still fail the public input boundary.
        req["items"][0]["analogue_history"] = [{"date": "2099-01-01", "quantity": 100000,
                                                 "source": "category"}]
        with self.assertRaises(ValueError):
            forecast_with_audit(req, "mean182")
        req["items"][0]["analogue_history"] = []
        req["items"][0]["known_promotions"] = [{"start_date": "1900-99-99", "end_date": "2026-04-05",
                                                    "announced_at": "2026-03-01", "planned_multiplier": 2,
                                                    "source": "commercial-plan"}]
        with self.assertRaises(ValueError):
            forecast_with_audit(req, "mean182")

    def test_known_trend_and_seasonality_are_learned_from_history(self):
        origin = date(2026, 4, 1).toordinal()
        days = np.arange(origin - 559, origin + 1, dtype=float)
        future = np.arange(origin + 1, origin + 29, dtype=float)
        def demand(values):
            return np.exp(3 + .3 * (values - origin) / 365.25 + .35 * np.sin(2*np.pi*values/365.25))
        predicted = fit_predict(days, demand(days), np.ones(len(days)), future, "seasonal560")
        self.assertLess(float(np.abs(predicted - demand(future)).sum() / demand(future).sum()), .01)


if __name__ == "__main__":
    unittest.main()
