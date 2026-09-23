"""Regression oracles from the independent audit, never calculator-as-oracle."""
from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path
import unittest

from model.testbed.business import check_cases, reference_calculate, validate_response


FIXTURES = json.loads((Path(__file__).parents[1] / "fixtures/business_cases.json").read_text())["cases"]
BY_ID = {case["id"]: case["input"] for case in FIXTURES}


class ProcurementCorrectionsTests(unittest.TestCase):
    def request(self, **changes):
        request = deepcopy(BY_ID["stock_base"])
        request["items"][0].update(changes)
        return request

    def assert_needs_data(self, request):
        result = reference_calculate(request)
        self.assertEqual(result["items"][0]["status"], "needs_data")
        self.assertIsNone(result["items"][0]["quantity"])
        self.assertTrue(result["approval_blocked"])
        self.assertIsNone(result["total_cost_kzt"])
        self.assertTrue(result["items"][0]["missing"])
        self.assertEqual(validate_response(request, result), [])

    def test_original_54_fixtures_remain_compatible(self):
        outcomes = check_cases(reference_calculate)
        self.assertEqual(len(outcomes), 54)
        for case in outcomes:
            with self.subTest(case=case["id"]):
                self.assertTrue(case["passed"], case["failures"])

    def test_exact_decimal_batch_boundaries(self):
        for tenths in range(1, 21):
            need = Decimal(tenths) / 10
            with self.subTest(need=need):
                request = self.request(unit="м", order_unit="м", unit_quantum=0.01,
                                       on_hand=float(Decimal(280) - need), min_order_qty=0.1, order_multiple=0.1)
                result = reference_calculate(request)
                self.assertEqual(Decimal(str(result["items"][0]["quantity"])), need)
                self.assertEqual(validate_response(request, result), [])

    def test_exact_decimal_money_boundaries(self):
        for quantity in range(1, 13):
            for cents in (1, 3, 7, 10):
                price = Decimal(cents) / 100
                budget = quantity * price
                with self.subTest(quantity=quantity, price=price):
                    request = self.request(on_hand=280 - quantity, unit_cost=float(price))
                    request["budget_kzt"] = float(budget)
                    result = reference_calculate(request)
                    self.assertEqual(Decimal(str(result["total_cost_kzt"])), budget)
                    self.assertEqual(result["budget_excess_kzt"], 0)
                    self.assertFalse(result["approval_blocked"])
                    self.assertEqual(result["rules"], [])
                    self.assertEqual(validate_response(request, result), [])

    def test_budget_across_suppliers_and_unknown_positive_price(self):
        request = deepcopy(BY_ID["two_suppliers_budget_total"])
        result = reference_calculate(request)
        self.assertEqual(result["known_cost_kzt"], 210 * 100 + 210 * 200)
        self.assertEqual(result["budget_excess_kzt"], 3000)
        self.assertTrue(result["approval_blocked"])
        request["items"][1]["unit_cost"] = None
        result = reference_calculate(request)
        self.assertEqual(result["known_cost_kzt"], 21000)
        self.assertIsNone(result["total_cost_kzt"])
        self.assertIn("BUDGET-01", result["rules"])
        self.assertEqual(validate_response(request, result), [])

    def test_late_inbound_exposes_shortage_after_proposed_order_eta(self):
        request = deepcopy(BY_ID["inbound_last_day"])
        result = reference_calculate(request)
        row = result["items"][0]
        self.assertEqual(row["quantity"], 110)  # Aggregate need stays comparable.
        self.assertEqual(row["first_deficit_day"], 8)  # Without proposed order.
        self.assertEqual(row["first_deficit_day_basis"], "without_proposed_order")
        # Independent path: stock70+order110 covers18 days; days19..27 lack90.
        self.assertEqual(row["residual_deficit_day"], 19)
        self.assertEqual(row["residual_shortfall"], 90)
        self.assertEqual(row["residual_unmet_quantity"], 90)
        self.assertTrue(row["requires_expedite"])
        self.assertIn("SUPPLY-02", row["rules"])
        self.assertIn("residual shortage 90", row["reason"])
        self.assertEqual(validate_response(request, result), [])

    def test_same_day_normal_arrival_and_early_gap_remain_distinct(self):
        same_day = reference_calculate(self.request(on_hand=60))["items"][0]
        self.assertEqual(same_day["first_deficit_day"], 7)
        self.assertEqual(same_day["early_shortfall"], 0)
        self.assertEqual(same_day["residual_shortfall"], 0)
        self.assertFalse(same_day["requires_expedite"])
        early = reference_calculate(self.request(on_hand=0))["items"][0]
        self.assertEqual(early["early_shortfall"], 60)
        self.assertEqual(early["residual_shortfall"], 0)
        self.assertTrue(early["requires_expedite"])

    def test_invalid_numeric_domains_and_types_block_quantity(self):
        changes = [
            {"daily_mean": [-10] * 28}, {"daily_mean": [True] * 28},
            {"daily_mean": [float("nan")] * 28}, {"daily_mean": [float("inf")] * 28},
            {"on_hand": -1}, {"reserved": -1}, {"unit_cost": -100},
            {"unit_cost": float("inf")}, {"on_hand": True}, {"reserved": "0"},
            {"lead_time_days": -1, "review_period_days": 29},
            {"lead_time_days": 29, "review_period_days": -1},
            {"lead_time_days": 7.0}, {"review_period_days": True},
            {"order_multiple": float("nan")}, {"min_order_qty": -1},
            {"inbound": [{"quantity": -20, "expected_at": "2026-04-05", "status": "confirmed"}]},
            {"material_requirements": [{"quantity": 50, "already_accounted_quantity": -20, "needed_at": "2026-04-10"}]},
            {"material_requirements": [{"quantity": 50, "already_accounted_quantity": 60, "needed_at": "2026-04-10"}]},
            {"economics": {"underage_cost": float("nan"), "overage_cost": 1, "horizon_days": 28, "source": "audit"}},
            {"growth_adjustments": [{"id": "g", "rate": -2, "valid_from": "2026-04-02", "valid_to": "2026-04-29"}]},
        ]
        for change in changes:
            with self.subTest(change=change):
                self.assert_needs_data(self.request(**change))
        for budget in (-1, True, "0", float("nan"), float("inf")):
            with self.subTest(budget=budget):
                request = self.request()
                request["budget_kzt"] = budget
                self.assert_needs_data(request)

    def test_invalid_dates_never_reduce_order_or_hide_commitments(self):
        for stamp in ("2026-04-05x", "20260405", "2026-02-30", "2026-04-05T00:00:00", None):
            with self.subTest(stamp=stamp):
                self.assert_needs_data(self.request(inbound=[{"quantity": 100, "expected_at": stamp, "status": "confirmed"}]))
                self.assert_needs_data(self.request(material_requirements=[{"quantity": 100, "already_accounted_quantity": 0, "needed_at": stamp}]))
        request = self.request()
        request["as_of"] = "20260401"
        with self.assertRaises(ValueError):
            reference_calculate(request)

    def test_quantile_one_with_accepted_mass_normalizes_consistently(self):
        request = self.request(horizon_distribution=[{"quantity": 280, "probability": 0.9999999999}],
                               economics={"underage_cost": 1, "overage_cost": 0, "horizon_days": 28, "source": "audit-owner-v3"})
        result = reference_calculate(request)
        self.assertEqual(result["items"][0]["target_quantile"], 1)
        self.assertEqual(result["items"][0]["target_stock"], 280)
        self.assertEqual(result["items"][0]["quantity"], 210)
        self.assertEqual(result["items"][0]["economics_source"], "audit-owner-v3")
        self.assertEqual(validate_response(request, result), [])
        request["items"][0]["horizon_distribution"][0]["probability"] = 0.9
        self.assert_needs_data(request)

    def test_zero_probability_support_does_not_set_boundary_quantile(self):
        request = self.request(horizon_distribution=[{"quantity": 0, "probability": 0},
                                                      {"quantity": 280, "probability": 1},
                                                      {"quantity": 999, "probability": 0}],
                               economics={"underage_cost": 0, "overage_cost": 1, "horizon_days": 28, "source": "boundary-economics"})
        row = reference_calculate(request)["items"][0]
        self.assertEqual(row["target_quantile"], 0)
        self.assertEqual(row["target_stock"], 280)
        request["items"][0]["economics"].update(underage_cost=1, overage_cost=0)
        self.assertEqual(reference_calculate(request)["items"][0]["target_stock"], 280)

    def test_unknown_constraint_quantity_is_explicitly_provisional(self):
        for change, quantity, minimum, multiple in [
            ({"on_hand": 69, "min_order_qty": None, "order_multiple": 5}, 211, None, 5),
            ({"on_hand": 279, "min_order_qty": 10, "order_multiple": None}, 1, 10, None),
        ]:
            with self.subTest(change=change):
                request = self.request(**change)
                result = reference_calculate(request)
                row = result["items"][0]
                self.assertEqual(row["quantity"], quantity)
                self.assertEqual(row["status"], "needs_review")
                self.assertEqual(row["quantity_kind"], "provisional_base_need")
                self.assertEqual(row["order_constraints_base"]["min_order_qty"], minimum)
                self.assertEqual(row["order_constraints_base"]["order_multiple"], multiple)
                self.assertTrue(result["approval_blocked"])
                self.assertIn("provisional", row["reason"])
                self.assertEqual(validate_response(request, result), [])

    def test_explanation_is_specific_and_orders_require_human_approval(self):
        first = reference_calculate(self.request())["items"][0]
        second_result = reference_calculate(self.request(on_hand=100))
        second = second_result["items"][0]
        self.assertNotEqual(first["reason"], second["reason"])
        for fragment in ("Forecast 280", "free stock 70", "quantity 210", "28 days"):
            self.assertIn(fragment, first["reason"])
        self.assertTrue(second_result["requires_manual_approval"])
        self.assertFalse(second_result["approval_blocked"])  # Eligible for review, not automatically ordered.
        precise = reference_calculate(self.request(economics={"underage_cost": 1, "overage_cost": 2,
                                                              "horizon_days": 28, "source": "audit"}))["items"][0]
        self.assertIn("quantile 0.333333", precise["reason"])
        self.assertNotIn("0.33333333333333333333", precise["reason"])
        self.assertAlmostEqual(precise["target_quantile"], 1 / 3, places=14)

    def test_explicit_urgency_and_shortage_priority(self):
        for changes, expected in [
            ({}, "routine"), ({"on_hand": 500}, "no_order"),
            ({"stock_current": False}, "data_required"),
            ({"min_order_qty": None}, "review_required"),
            ({"on_hand": 0}, "expedite"),
            ({"on_hand": 0, "min_order_qty": None}, "expedite"),
        ]:
            with self.subTest(changes=changes):
                request = self.request(**changes)
                result = reference_calculate(request)
                self.assertEqual(result["items"][0]["urgency"], expected)
                self.assertEqual(validate_response(request, result), [])
                result["items"][0]["urgency"] = "invented"
                self.assertTrue(validate_response(request, result))
        late = reference_calculate(deepcopy(BY_ID["inbound_last_day"]))
        self.assertEqual(late["items"][0]["urgency"], "expedite")

    def test_checker_rejects_all_changed_invalid_responses(self):
        def status(row):
            if row["status"] == "ok":
                row["status"] = "needs_data"
        def rules(row):
            row["rules"] = " ".join(row["rules"])
        def supplier(row):
            row["supplier_id"] = "wrong-supplier"
        def reason(row):
            row["reason"] = True
        def quantity(row):
            if row["quantity"] is not None:
                row["quantity"] = -1
        def unit(row):
            if row["quantity"] is not None:
                row["unit"] = "wrong-unit"
        for mutation in (status, rules, supplier, reason, quantity, unit):
            changed = set()
            def adapter(request):
                result = reference_calculate(request)
                before = deepcopy(result)
                for row in result["items"]:
                    mutation(row)
                if result != before:
                    changed.add(json.dumps(request, sort_keys=True))
                return result
            outcomes = check_cases(adapter)
            changed_ids = {case["id"] for case in FIXTURES if json.dumps(case["input"], sort_keys=True) in changed}
            self.assertTrue(changed_ids)
            with self.subTest(mutation=mutation.__name__):
                self.assertEqual([r["id"] for r in outcomes if r["id"] in changed_ids and r["passed"]], [])

    def test_checker_rejects_missing_fields_and_cross_field_conflicts(self):
        request = self.request()
        correct = reference_calculate(request)
        for field in ("quantity", "unit_cost", "economics_source", "first_deficit_day", "missing"):
            result = deepcopy(correct)
            del result["items"][0][field]
            with self.subTest(field=field):
                self.assertTrue(validate_response(request, result))
        for mutate in (
            lambda r: r.update(known_cost_kzt=0),
            lambda r: r.update(total_cost_kzt=None),
            lambda r: r.update(supplier_groups={"supplier-X": ["0001"]}),
            lambda r: r.update(requires_manual_approval=False),
            lambda r: r["items"][0].update(quantity=210.5),
            lambda r: r["items"][0].update(first_deficit_day=29),
            lambda r: r["items"][0].update(economics_source="invented"),
            lambda r: r["items"][0].update(target_stock=float("nan")),
            lambda r: r["items"][0].update(rules=["invented-rule"]),
        ):
            result = deepcopy(correct)
            mutate(result)
            self.assertTrue(validate_response(request, result))


if __name__ == "__main__":
    unittest.main()
