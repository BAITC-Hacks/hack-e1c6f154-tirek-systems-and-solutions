"""Handwritten past-only probes; no generator parameters, seeds or future truth."""
from copy import deepcopy
from datetime import date, timedelta
import statistics
import unittest

from model.testbed.adapters import call_external, validate_request
from model.testbed.seasonal import forecast


CUTOFF = date(2025, 7, 1)


def request_with_history(quantity=lambda offset, stamp: 10, days=560):
    item = {"sku": "manual-item", "unit": "шт", "warehouse_id": "manual-warehouse",
            "supplier_id": "manual-supplier", "category_raw": "C1",
            "launch_date": (CUTOFF - timedelta(days=days - 1)).isoformat(),
            "history": [], "events": [], "known_promotions": [], "analogue_history": []}
    for offset in range(-days + 1, 1):
        stamp = CUTOFF + timedelta(days=offset)
        observed = quantity(offset, stamp)
        item["history"].append({"date": stamp.isoformat(), "observed_quantity": observed,
                                "availability_fraction": 1.0, "complete": True})
        if observed:
            item["events"].append({"event_id": f"opaque-document-{len(item['events'])}",
                                   "date": stamp.isoformat(), "client_id": "regular-client", "quantity": observed})
    return {"schema_version": "forecast-input-v2", "as_of": CUTOFF.isoformat(), "horizon_days": 28, "items": [item]}


class SeasonalExampleTests(unittest.TestCase):
    def test_past_seasonal_peak_is_retained_instead_of_flat_recent_mean(self):
        request = request_with_history(lambda offset, stamp: 100 if -350 <= offset <= -322 else 10)
        values = forecast(request)["manual-item"]
        self.assertEqual(statistics.mean(r["observed_quantity"] for r in request["items"][0]["history"][-56:]), 10)
        self.assertGreater(max(values), 50)
        self.assertGreater(max(values), min(values))

    def test_sustained_growth_raises_forecast_above_last_observed_level(self):
        request = request_with_history(lambda offset, stamp: 20 * 1.002**offset)
        values = forecast(request)["manual-item"]
        self.assertGreater(min(values), request["items"][0]["history"][-1]["observed_quantity"])
        self.assertGreater(values[-1], values[0])

    def test_project_split_across_documents_and_days_does_not_change_forecast(self):
        clean = request_with_history()
        changed = deepcopy(clean)
        item = changed["items"][0]
        by_date = {r["date"]: r for r in item["history"]}
        # Exercise both recent growth observations and prior-year seasonal anchors.
        for anchor in (-18, -350):
            for offset in range(anchor, anchor + 3):
                stamp = (CUTOFF + timedelta(days=offset)).isoformat()
                by_date[stamp]["observed_quantity"] += 300
                for document in range(5):
                    item["events"].append({"event_id": f"opaque-added-{offset}-{document}", "date": stamp,
                                           "client_id": f"one-off-client-{anchor}", "quantity": 60})
        validate_request(changed)
        self.assertEqual(forecast(clean), forecast(changed))

    def test_regular_large_customer_preserves_weekly_demand(self):
        request = request_with_history()
        item = request["items"][0]
        for row in item["history"]:
            if date.fromisoformat(row["date"]).weekday() == 1:
                row["observed_quantity"] += 90
                item["events"].append({"event_id": "large-" + row["date"], "date": row["date"],
                                       "client_id": "weekly-large-client", "quantity": 90})
        validate_request(request)
        values = forecast(request)["manual-item"]
        self.assertEqual(sum(values), 640)
        for day, value in enumerate(values, 1):
            self.assertEqual(value, 100 if (CUTOFF + timedelta(days=day)).weekday() == 1 else 10)

    def test_partial_and_full_unavailability_are_compensated(self):
        for fraction in (.25, 0):
            request = request_with_history(lambda offset, stamp: 20)
            item = request["items"][0]
            changed_dates = set()
            for row in item["history"]:
                offset = (date.fromisoformat(row["date"]) - CUTOFF).days
                if -48 <= offset <= -7:
                    row["availability_fraction"] = fraction
                    row["observed_quantity"] *= fraction
                    changed_dates.add(row["date"])
            for event in item["events"]:
                if event["date"] in changed_dates:
                    event["quantity"] *= fraction
            validate_request(request)
            self.assertEqual(forecast(request)["manual-item"], [20] * 28)
            naive = deepcopy(request)
            for row in naive["items"][0]["history"]:
                row["availability_fraction"] = 1
            self.assertLess(sum(forecast(naive)["manual-item"]), 20 * 28)

    def test_known_promotion_is_removed_from_history_and_applied_to_forecast(self):
        request = request_with_history()
        item = request["items"][0]
        historical_dates = {(CUTOFF + timedelta(days=d)).isoformat() for d in range(-45, -38)}
        for row in item["history"]:
            if row["date"] in historical_dates:
                row["observed_quantity"] *= 1.8
        for event in item["events"]:
            if event["date"] in historical_dates:
                event["quantity"] *= 1.8
        for start, end in ((-45, -39), (5, 11)):
            item["known_promotions"].append({"start_date": (CUTOFF + timedelta(days=start)).isoformat(),
                                            "end_date": (CUTOFF + timedelta(days=end)).isoformat(),
                                            "announced_at": (CUTOFF - timedelta(days=60)).isoformat(),
                                            "planned_multiplier": 1.8, "source": "manual-plan"})
        validate_request(request)
        self.assertEqual(forecast(request)["manual-item"], [18 if 5 <= d <= 11 else 10 for d in range(1, 29)])

    def test_no_history_uses_explicit_past_analogue_and_never_invents_zero(self):
        request = request_with_history(days=1)
        item = request["items"][0]
        item["history"] = []
        item["events"] = []
        item["analogue_history"] = [{"date": CUTOFF.isoformat(), "quantity": 12, "source": "manual-analogue"}]
        validate_request(request)
        self.assertEqual(forecast(request)["manual-item"], [12] * 28)
        item["analogue_history"] = []
        with self.assertRaisesRegex(ValueError, "demand is unknown"):
            forecast(request)

    def test_short_history_falls_back_to_recent_available_mean(self):
        request = request_with_history(lambda offset, stamp: 17, days=90)
        self.assertEqual(forecast(request)["manual-item"], [17] * 28)

    def test_observed_zero_is_valid_but_incomplete_day_is_not_zero_demand(self):
        request = request_with_history(lambda offset, stamp: 0, days=90)
        self.assertEqual(forecast(request)["manual-item"], [0] * 28)
        request = request_with_history(days=90)
        request["items"][0]["history"][-1].update(complete=False, observed_quantity=1e9)
        self.assertEqual(forecast(request)["manual-item"], [10] * 28)

    def test_permutations_and_changed_event_ids_do_not_change_forecast(self):
        request = request_with_history(lambda offset, stamp: 10 + stamp.weekday())
        expected = forecast(request)
        changed = deepcopy(request)
        for collection in ("history", "events", "known_promotions", "analogue_history"):
            changed["items"][0][collection].reverse()
        for index, event in enumerate(changed["items"][0]["events"]):
            event["event_id"] = f"unrelated-id-{index}"
        self.assertEqual(forecast(changed), expected)

    def test_direct_call_does_not_consume_future_observations_or_unannounced_plan(self):
        request = request_with_history()
        expected = forecast(request)
        item = request["items"][0]
        future = (CUTOFF + timedelta(days=1)).isoformat()
        item["history"].append({"date": future, "observed_quantity": 1e9, "availability_fraction": 1, "complete": True})
        item["events"].append({"event_id": "future-opaque", "date": future, "client_id": "new-client", "quantity": 1e9})
        item["analogue_history"].append({"date": future, "quantity": 1e9, "source": "future-analogue"})
        item["known_promotions"].append({"start_date": future, "end_date": future, "announced_at": future,
                                        "planned_multiplier": 10, "source": "unannounced"})
        self.assertEqual(forecast(request), expected)

    def test_external_adapter_contract_works_without_a_different_runner(self):
        request = request_with_history(days=90)
        self.assertEqual(call_external("model.testbed.seasonal:forecast", request), forecast(request))


if __name__ == "__main__":
    unittest.main()
