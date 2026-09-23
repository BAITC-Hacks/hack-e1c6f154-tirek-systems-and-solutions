"""Second adversarial pass: source-based semantics and exact numeric boundaries."""
from copy import deepcopy
from decimal import Decimal, ROUND_DOWN, localcontext
import json
from pathlib import Path
import unittest

from model.testbed.business import check_cases, compare, reference_calculate, round_up, validate_response


BASE = json.loads((Path(__file__).parents[1] / "fixtures/business_cases.json").read_text())["cases"][0]["input"]


class FullBusinessTests(unittest.TestCase):
    def request(self, **changes):
        result = deepcopy(BASE)
        result["items"][0].update(changes)
        return result

    def assert_data_error(self, request, field=None):
        result = reference_calculate(request)
        self.assertTrue(result["approval_blocked"])
        self.assertIsNone(result["total_cost_kzt"])
        for row in result["items"]:
            self.assertEqual(row["status"], "needs_data")
            self.assertIsNone(row["quantity"])
            if field:
                self.assertIn(field, row["missing"])
        self.assertEqual(validate_response(request, result), [])
        json.dumps(result, allow_nan=False)
        return result

    def test_empty_request_cannot_bypass_invalid_budget(self):
        for budget in (-1, "unknown", True, float("nan"), float("inf"), 10 ** 400):
            with self.subTest(budget=repr(budget)):
                request = deepcopy(BASE)
                request.update(items=[], budget_kzt=budget)
                with self.assertRaises(ValueError):
                    reference_calculate(request)
                self.assertTrue(validate_response(request, {}))
                request["items"] = deepcopy(BASE["items"])
                self.assert_data_error(request, "valid_budget_kzt")
        empty = deepcopy(BASE)
        empty.update(items=[], budget_kzt=0)
        self.assertEqual(reference_calculate(empty)["known_cost_kzt"], 0)

    def test_bad_envelopes_and_out_of_calendar_horizon_are_value_errors(self):
        for request in (None, [], {}, dict(BASE, items=None), dict(BASE, category_policies=[])):
            with self.subTest(request=repr(request)):
                with self.assertRaises(ValueError):
                    reference_calculate(request)
                self.assertTrue(validate_response(request, {}))
        for horizon in (True, 0, -1, 28.0, "28", 10 ** 100, None):
            with self.subTest(horizon=horizon):
                with self.assertRaises(ValueError):
                    reference_calculate(dict(BASE, horizon_days=horizon))
        with self.assertRaises(ValueError):
            reference_calculate(dict(BASE, as_of="9999-12-31"))

    def test_signed_net_stock_equals_gross_minus_reserve(self):
        for gross, reserved in ((0, 20), (5, 20), (0, 1), (10, 10), (70, 20)):
            with self.subTest(gross=gross, reserved=reserved):
                net = gross - reserved
                gross_request = self.request(on_hand=gross, reserved=reserved)
                net_request = self.request(on_hand=None, reserved=None, free_stock=net)
                a, b = reference_calculate(gross_request), reference_calculate(net_request)
                for field in ("quantity", "free_stock", "early_shortfall", "residual_shortfall", "residual_unmet_quantity"):
                    self.assertEqual(a["items"][0][field], b["items"][0][field])
                self.assertEqual(a["items"][0]["quantity"], 280 - net)
                self.assertEqual(validate_response(net_request, b), [])

    def test_zero_daily_mean_positive_distribution_is_unknown_not_ignored_growth(self):
        for growth in ([], [{"id": "g", "rate": 1, "valid_from": "2026-04-02", "valid_to": "2026-04-29"}]):
            with self.subTest(growth=growth):
                self.assert_data_error(self.request(daily_mean=[0] * 28, growth_adjustments=growth),
                                       "consistent_daily_distribution")
        request = self.request(daily_mean=[0] * 28, horizon_distribution=[{"quantity": 0, "probability": 1}],
                               material_requirements=[{"quantity": 7, "already_accounted_quantity": 0, "needed_at": "2026-04-10"}],
                               on_hand=0)
        result = reference_calculate(request)
        self.assertEqual(result["items"][0]["quantity"], 7)
        self.assertEqual(result["items"][0]["forecast_mean"], 0)
        self.assertEqual(validate_response(request, result), [])

    def test_huge_values_cannot_escape_or_create_infinity(self):
        self.assert_data_error(self.request(unit_cost=1e308), "numeric_range")
        self.assert_data_error(self.request(unit_cost=10 ** 400), "valid_unit_cost")
        self.assert_data_error(self.request(unit_quantum=Decimal("1e-1000")), "unit_quantum")
        self.assert_data_error(self.request(unit_cost=Decimal("0.123456789012345678901")), "price_representation")
        request = self.request(on_hand=0, daily_mean=[1] * 28,
                               horizon_distribution=[{"quantity": 1, "probability": 1}], unit_cost=1e308)
        second = deepcopy(request["items"][0])
        second.update(sku="0002", supplier_id="supplier-B")
        request["items"].append(second)
        result = self.assert_data_error(request, "aggregate_numeric_range")
        self.assertEqual(result["known_cost_kzt"], 0)  # No calculated quantity survives; total is null.

    def test_huge_exact_lot_is_serialized_as_int_and_validates(self):
        request = self.request(unit_quantum=1e-100, min_order_qty=0, order_multiple=1e100)
        result = reference_calculate(request)
        self.assertEqual(result["items"][0]["quantity"], 10 ** 100)
        self.assertIsInstance(result["items"][0]["quantity"], int)
        restored = json.loads(json.dumps(result, allow_nan=False))
        self.assertEqual(restored["items"][0]["quantity"], 10 ** 100)
        self.assertEqual(validate_response(request, restored), [])
        restored["items"][0]["quantity"] += 1
        self.assertTrue(validate_response(request, restored))

    def test_caller_decimal_context_cannot_change_results_or_comparison(self):
        request = self.request(unit="м", order_unit="м", unit_quantum=0.01,
                               on_hand=279.9, min_order_qty=0.1, order_multiple=0.1,
                               economics={"underage_cost": 1, "overage_cost": 2, "horizon_days": 28, "source": "audit"})
        expected = reference_calculate(request)
        with localcontext() as context:
            context.prec = 2
            context.Emax = 2
            context.Emin = -2
            context.rounding = ROUND_DOWN
            actual = reference_calculate(request)
            self.assertEqual(expected, actual)
            self.assertEqual(validate_response(request, actual), [])
            self.assertEqual(Decimal(str(round_up(Decimal("0.1"), Decimal("0.1")))), Decimal("0.1"))
            self.assertEqual(compare({"quantity": 123456789}, {"quantity": 123456789}), [])
            self.assertTrue(compare({"quantity": 123456789}, {"quantity": 123400000}))

    def test_lead_zero_and_last_calendar_day(self):
        request = self.request(on_hand=0, lead_time_days=0, review_period_days=1,
                               daily_mean=[10], horizon_distribution=[{"quantity": 10, "probability": 1}])
        request.update(horizon_days=1, as_of="9999-12-30")
        result = reference_calculate(request)
        row = result["items"][0]
        self.assertEqual(row["quantity"], 10)
        self.assertEqual(row["first_deficit_day"], 1)
        self.assertEqual(row["early_shortfall"], 0)
        self.assertEqual(row["residual_shortfall"], 0)
        self.assertFalse(row["requires_expedite"])
        self.assertEqual(validate_response(request, result), [])

    def test_pack_conversion_and_quantile_boundaries(self):
        request = self.request(on_hand=255, order_unit="box", order_to_base_factor=10,
                               min_order_qty=2, order_multiple=2)
        result = reference_calculate(request)
        self.assertEqual(result["items"][0]["quantity"], 40)  # Need25 pieces; lots20 =>40.
        self.assertEqual(validate_response(request, result), [])
        distribution = [{"quantity": 0, "probability": 0.2}, {"quantity": 100, "probability": 0.6},
                        {"quantity": 200, "probability": 0.2}]
        for underage, overage, target in ((1, 4, 0), (4, 1, 100), (9, 1, 200), (1, 0, 200)):
            request = self.request(horizon_distribution=distribution,
                                   economics={"underage_cost": underage, "overage_cost": overage,
                                              "horizon_days": 28, "source": "audit"})
            row = reference_calculate(request)["items"][0]
            self.assertEqual(row["target_stock"], target)

    def test_coherent_output_tampering_is_not_a_contract_pass(self):
        request = self.request()
        good = reference_calculate(request)
        tampered = deepcopy(good)
        tampered["items"][0]["quantity"] = 999
        tampered["known_cost_kzt"] = tampered["total_cost_kzt"] = 99900
        self.assertTrue(validate_response(request, tampered))
        for changes in (
            {"target_stock": 1069, "quantity": 999},
            {"free_stock": 0, "quantity": 280},
            {"forecast_mean": 10000}, {"target_quantile": 0.1},
            {"material_uncovered": 100, "quantity": 310},
            {"inbound_in_horizon": 100, "quantity": 110},
            {"residual_shortfall": 0, "residual_deficit_day": None, "residual_unmet_quantity": 0},
        ):
            current_request = request
            if "residual_shortfall" in changes:
                current_request = self.request(inbound=[{"quantity": 100, "expected_at": "2026-04-29", "status": "confirmed"}])
            result = reference_calculate(current_request)
            result["items"][0].update(changes)
            if "quantity" in changes:
                result["known_cost_kzt"] = result["total_cost_kzt"] = changes["quantity"] * 100
            with self.subTest(changes=changes):
                self.assertTrue(validate_response(current_request, result))

    def test_invented_missing_data_or_false_review_status_is_rejected(self):
        request = self.request()
        result = reference_calculate(request)
        result["items"][0].update(status="needs_data", quantity=None, missing=["invented"],
                                   rules=["DATA-01"], urgency="data_required")
        result.update(known_cost_kzt=0, total_cost_kzt=None, approval_blocked=True)
        self.assertTrue(validate_response(request, result))
        result = reference_calculate(request)
        result["items"][0].update(status="needs_review", urgency="review_required")
        result["approval_blocked"] = True
        self.assertTrue(validate_response(request, result))
        # A fake calculated answer cannot hide stale source stock.
        stale = self.request(stock_current=False)
        self.assertTrue(validate_response(stale, reference_calculate(request)))
        for false_rule in ("DATA-01", "POLICY-01", "DEMAND-04", "SUPPLY-04", "BUDGET-01"):
            result = reference_calculate(request)
            result["items"][0]["rules"].append(false_rule)
            with self.subTest(false_rule=false_rule):
                self.assertTrue(validate_response(request, result))

    def test_json_type_mutations_return_errors_not_type_or_attribute_exceptions(self):
        request = self.request()
        good = reference_calculate(request)
        for replacement in (None, True, 0, "bad", [], {}, [None], [{}], [1]):
            for key in ("rules", "items", "supplier_groups", "approval_blocked"):
                current = deepcopy(good)
                current[key] = replacement
                if current == good:
                    continue
                with self.subTest(key=key, replacement=replacement):
                    self.assertTrue(validate_response(request, current))
            for key in ("status", "rules", "missing", "sku", "quantity", "unit_cost", "urgency"):
                current = deepcopy(good)
                current["items"][0][key] = replacement
                if current == good:
                    continue
                with self.subTest(item_key=key, replacement=replacement):
                    self.assertTrue(validate_response(request, current))
        self.assert_data_error(self.request(inbound=[{"quantity": 1, "expected_at": "2026-04-05", "status": []}]),
                               "valid_inbound")

    def test_242_input_field_mutations_are_controlled_and_self_consistent(self):
        values = (None, True, False, 0, -1, [], {}, [{}], "bad", float("inf"), float("nan"))
        count = 0
        for key in BASE["items"][0]:
            for value in values:
                count += 1
                request = self.request(**{key: value})
                with self.subTest(field=key, value=repr(value)):
                    if key in {"sku", "supplier_id", "category_raw"} and not isinstance(value, str):
                        with self.assertRaises(ValueError):
                            reference_calculate(request)
                        continue
                    result = reference_calculate(request)
                    json.dumps(result, allow_nan=False)
                    self.assertEqual(validate_response(request, result), [])
        self.assertEqual(count, 242)

    def test_frozen_fixture_expectations_still_hold(self):
        outcomes = check_cases(reference_calculate)
        self.assertEqual(len(outcomes), 54)
        self.assertEqual([row["id"] for row in outcomes if not row["passed"]], [])


if __name__ == "__main__":
    unittest.main()
