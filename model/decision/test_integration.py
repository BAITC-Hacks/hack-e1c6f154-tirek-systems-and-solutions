"""Cross-module acceptance tests using explicit synthetic scenario assumptions.

One point forecast repeated as one deterministic path here is a test fixture,
not a calibrated predictive distribution or a claim about production service.
"""
from copy import deepcopy
from dataclasses import replace
from datetime import date, timedelta
import unittest

from model.regular_forecast.predictor import forecast_with_audit
from model.regular_forecast.test_predictor import request as forecast_fixture
from model.decision.core import (
    RecommendationInput, InventorySnapshot, SupplierConstraint, ServicePolicy,
    GrowthAdjustment, Inbound, recommend, group_by_supplier,
)
from model.decision.rules import build_ai_context, stock_levels, validate_ai_judgement


def calculation(forecast_request, **changes):
    predictions, _ = forecast_with_audit(forecast_request, "mean182")
    as_of = date.fromisoformat(forecast_request["as_of"])
    options = dict(sku="x", warehouse_id="test", supplier_id="s1", unit="шт", unit_quantum=1,
                   as_of=as_of, forecast_as_of=as_of, forecast_id="synthetic-regular-fixture",
                   scenarios=[predictions["x"]], inventory=InventorySnapshot(as_of, 100, 0, source="fixture"),
                   lead_time_days=7, review_period_days=21,
                   constraints=SupplierConstraint("шт", 0, 1, source="fixture"),
                   service_policy=ServicePolicy(.9, "explicit-synthetic-policy", "1"))
    options.update(changes)
    return recommend(RecommendationInput(**options))


class IntegrationTests(unittest.TestCase):
    def test_forecast_stock_and_dated_inbound_change_the_real_calculator(self):
        request = forecast_fixture()
        as_of = date.fromisoformat(request["as_of"])
        baseline = calculation(request)
        more_stock = calculation(request, inventory=InventorySnapshot(as_of, 130, 0))
        timely = calculation(request, inbound=[Inbound("receipt", 30, as_of+timedelta(days=9), "шт", "fixture")])
        late = calculation(request, inbound=[Inbound("receipt", 30, as_of+timedelta(days=40), "шт", "fixture")])
        self.assertEqual(baseline.quantity, 180)
        self.assertEqual(more_stock.quantity, 150)
        self.assertEqual(timely.quantity, 150)
        self.assertEqual(late.quantity, 180)
        self.assertFalse(late.diagnostics["inbound"][0]["in_horizon"])

    def test_stockout_correction_flows_through_to_quantity(self):
        request = forecast_fixture()
        days = set()
        for row in request["items"][0]["history"][-42:]:
            row.update(observed_quantity=0, availability_fraction=0)
            days.add(row["date"])
        for event in request["items"][0]["events"]:
            if event["date"] in days:
                event["quantity"] = 0
        raw = deepcopy(request)
        for row in raw["items"][0]["history"]:
            row["availability_fraction"] = 1
        self.assertGreater(calculation(request).quantity, calculation(raw).quantity)

    def test_existing_client_split_project_does_not_raise_order(self):
        request = forecast_fixture()
        baseline = calculation(request)
        row = request["items"][0]["history"][-18]
        row["observed_quantity"] += 500
        for i in range(5):
            request["items"][0]["events"].append({"date": row["date"], "quantity": 100,
                                                    "event_id": f"project-{i}", "client_id": "returning"})
        self.assertEqual(calculation(request).quantity, baseline.quantity)

    def test_growth_is_applied_once_and_limited_to_its_dates(self):
        request = forecast_fixture()
        as_of = date.fromisoformat(request["as_of"])
        adjustment = GrowthAdjustment("commercial-forecast", 1, as_of+timedelta(days=1),
                                      as_of+timedelta(days=14), "fixture")
        changed = calculation(request, growth_adjustments=[adjustment])
        already = calculation(request, growth_adjustments=[adjustment], included_growth_ids=[adjustment.adjustment_id])
        self.assertAlmostEqual(changed.diagnostics["regular_demand_mean"], 420)
        self.assertAlmostEqual(already.diagnostics["regular_demand_mean"], 280)
        self.assertGreater(changed.quantity, already.quantity)

    def test_grouping_and_unavailable_ai_preserve_explained_quantity(self):
        first = calculation(forecast_fixture())
        second = replace(first, sku="other", supplier_id="s2")
        groups = group_by_supplier([first, second])
        self.assertEqual(set(groups), {"s1", "s2"})
        self.assertTrue(all(r.reason for rows in groups.values() for r in rows))
        context = build_ai_context(
            evidence=[{"id": "quantity", "source_kind": "synthetic", "reference": "fixture:result",
                       "label": "Recommended quantity", "value": first.quantity, "unit": "шт"}],
            versions={"dataset_version": "fixture-1", "policy_version": "1", "calculation_revision": 1},
            allowed_rule_ids=["POLICY-02"], recommended_quantity=first.quantity)
        judgement = validate_ai_judgement(None, request_context=context, current_context=context)
        self.assertEqual(judgement["status"], "unavailable")
        self.assertEqual(first.quantity, 180)
        level = stock_levels(on_hand=59, reserved=20, reference_stock=100)
        self.assertEqual(level["observed_level"], "warning")
        self.assertEqual(level["remaining_pct"], 39)


if __name__ == "__main__":
    unittest.main()
