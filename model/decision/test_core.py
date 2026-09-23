"""Independent day-by-day oracle and business invariants, not forecast accuracy."""
from dataclasses import replace
from datetime import date, timedelta
import math
import random
import unittest

from model.decision.core import (
    EconomicProfile, GrowthAdjustment, Inbound, InventorySnapshot,
    MaterialRequirement, RecommendationInput, ServicePolicy, SupplierConstraint,
    check_approval, group_by_supplier, recommend,
)


AS_OF = date(2026, 1, 1)


def request(**changes):
    defaults = dict(
        sku="001", warehouse_id="ALM", supplier_id="S1", unit="шт",
        as_of=AS_OF, forecast_as_of=AS_OF, forecast_id="test-forecast",
        unit_quantum=1,
        scenarios=((5, 5, 5, 5),), lead_time_days=1, review_period_days=3,
        inventory=InventorySnapshot(AS_OF, on_hand=0, reserved=0, source="erp:1"),
        constraints=SupplierConstraint("шт", min_order_qty=0, order_multiple=1,
                                       source="supplier:1"),
        economics=EconomicProfile(9, 1, 4, "KZT", "explicit scenario"),
        unit_cost=10, price_currency="KZT", data_version="data-1",
    )
    defaults.update(changes)
    return RecommendationInput(**defaults)


def oracle(req, quantity):
    """Independent inventory flow, explicitly inserting Q (no core helpers).

    Returns per-scenario total loss, final stock, and post-arrival hard loss.
    Only used for fully specified validated cases in this file.
    """
    losses, endings, hard_losses = [], [], []
    inv = req.inventory
    free = inv.free_stock if inv.free_stock is not None else inv.on_hand - inv.reserved
    for path in req.scenarios:
        stock = free + (quantity if req.lead_time_days == 0 else 0)
        loss = hard_loss = 0
        for index, demand in enumerate(path):
            day = req.as_of + timedelta(days=index + 1)
            if index + 1 == req.lead_time_days:
                stock += quantity
            stock += sum(x.quantity for x in req.inbound if x.expected_at == day)
            material = [x for x in req.materials if x.due_at == day]
            # Independent explicit dispatch: hard material first, then other demand.
            hard = sum(x.quantity - x.already_reserved
                       for x in material if x.hard)
            served = min(hard, stock)
            stock -= served
            loss += hard - served
            if index + 1 >= req.lead_time_days:
                hard_loss += hard - served
            adjusted = demand - sum(x.included_in_forecast for x in material)
            for growth in req.growth_adjustments:
                if growth.adjustment_id not in req.included_growth_ids and growth.starts_at <= day <= growth.ends_at:
                    adjusted *= 1 + growth.rate
            adjusted += sum(x.quantity - x.already_reserved
                            for x in material if not x.hard)
            served = min(adjusted, stock)
            stock -= served
            loss += adjusted - served
        losses.append(loss)
        endings.append(stock)
        hard_losses.append(hard_loss)
    return losses, endings, hard_losses


class DecisionCases(unittest.TestCase):
    def test_deterministic_need_and_reserve_only_once(self):
        base = request(inventory=InventorySnapshot(AS_OF, 12, 2, 10))
        self.assertEqual(recommend(base).quantity, 10)
        free_only = replace(base, inventory=InventorySnapshot(AS_OF, reserved=2, free_stock=10))
        self.assertEqual(recommend(free_only).quantity, 10)
        self.assertEqual(recommend(base).diagnostics["free_stock"], 10)

    def test_moq_is_a_discrete_economic_choice_not_always_round_up(self):
        req = request(scenarios=((0, 0, 0, 2),),
                      constraints=SupplierConstraint("шт", 10, 10),
                      economics=EconomicProfile(1, 2, 4, "KZT", "test"))
        self.assertEqual(recommend(req).quantity, 0)
        req = replace(req, economics=EconomicProfile(9, 1, 4, "KZT", "test"))
        self.assertEqual(recommend(req).quantity, 10)

    def test_different_economics_change_quantity_for_same_demand(self):
        low = request(scenarios=((0, 0, 0, 2), (0, 0, 0, 10)),
                      economics=EconomicProfile(1, 9, 4, "KZT", "test"))
        high = replace(low, economics=EconomicProfile(9, 1, 4, "KZT", "test"))
        self.assertEqual(recommend(low).quantity, 2)
        self.assertEqual(recommend(high).quantity, 10)
        self.assertGreater(recommend(high).quantity, recommend(low).quantity)

    def test_tie_prefers_less_capital_and_no_invented_purchase_loss(self):
        req = request(scenarios=((0, 0, 0, 0), (0, 0, 0, 10)),
                      economics=EconomicProfile(1, 1, 4, "KZT", "test"))
        self.assertEqual(recommend(req).quantity, 0)
        self.assertEqual(recommend(replace(req, unit_cost=1000000)).quantity, 0)
        self.assertEqual(recommend(replace(req, economics=EconomicProfile(2, 1, 4, "KZT", "test"))).quantity, 10)

    def test_decimal_economic_ties_choose_smallest_valid_quantity(self):
        req = request(scenarios=tuple((0, 0, 0, x) for x in (0.2, 0.4, 2.9, 2.5, 1.5, 2.4)),
                      unit="кг", unit_quantum=0.1, constraints=SupplierConstraint("кг", 0.5, 0.2),
                      economics=EconomicProfile(0.5, 0.5, 4, "KZT", "test"))
        self.assertEqual(recommend(req).quantity, 1.6)

    def test_joint_daily_paths_not_sum_of_daily_quantiles(self):
        req = request(scenarios=((10, 0, 0, 0), (0, 10, 0, 0)),
                      economics=None, service_policy=ServicePolicy(1, "category", "v1"))
        # Sum of daily max would be 20, but every entire path needs only 10.
        self.assertEqual(recommend(req).quantity, 10)
        self.assertEqual(recommend(req).diagnostics["post_arrival_no_shortage_fraction"], 1)

    def test_decimal_service_rank_does_not_select_extra_scenario(self):
        for count, fraction, expected in ((25, .56, 14), (25, .28, 7), (50, .14, 7), (100, .07, 7)):
            with self.subTest(count=count, fraction=fraction):
                req = request(scenarios=tuple((0, 0, 0, x) for x in range(1, count+1)), economics=None,
                              service_policy=ServicePolicy(fraction, "test", "v1"))
                self.assertEqual(recommend(req).quantity, expected)

    def test_individual_policy_cannot_lower_explicit_category_floor(self):
        req = request(scenarios=((0, 0, 0, 2), (0, 0, 0, 10)),
                      economics=EconomicProfile(1, 9, 4, "KZT", "test"),
                      category_id="critical", category_policies={"critical": ServicePolicy(1, "category", "v1")},
                      service_policy=ServicePolicy(0.1, "sku", "v2"))
        row = recommend(req)
        self.assertEqual(row.quantity, 10)
        self.assertEqual(row.diagnostics["minimum_service_level"], 1)
        self.assertEqual(row.diagnostics["policy_sources"], ["sku", "category"])

    def test_service_fallback_has_no_fake_economics(self):
        row = recommend(request(economics=None, service_policy=ServicePolicy(0.9, "approved", "v1")))
        self.assertEqual(row.quantity, 20)
        self.assertIsNone(row.diagnostics["expected_loss_cost"])
        self.assertIsNone(row.diagnostics["economic_critical_fraction"])

    def test_missing_essentials_are_not_zero_defaults(self):
        cases = [dict(inventory=None), dict(lead_time_days=None),
                 dict(review_period_days=None), dict(economics=None),
                 dict(scenarios=None), dict(inventory=InventorySnapshot(AS_OF, on_hand=10))]
        for change in cases:
            with self.subTest(change=change):
                row = recommend(request(**change))
                self.assertEqual(row.status, "needs_data")
                self.assertIsNone(row.quantity)
                self.assertIsNone(row.provisional_quantity)
                self.assertTrue(row.required_fields)

    def test_unknown_constraints_give_provisional_and_block_approval(self):
        row = recommend(request(constraints=None))
        self.assertEqual(row.status, "needs_review")
        self.assertIsNone(row.quantity)
        self.assertEqual(row.provisional_quantity, 20)
        self.assertIsNone(row.diagnostics["order_multiple_inventory_units"])
        verdict = check_approval([row], current_revision=1, expected_revision=1,
                                 confirmed_by="manager", review_reasons={("S1", "ALM", "001"): "checked"})
        self.assertFalse(verdict.allowed)
        self.assertIn("unconfirmed_quantity:S1:ALM:001", verdict.reasons)

    def test_known_multiple_remains_active_when_minimum_unknown(self):
        row = recommend(request(scenarios=((0, 0, 0, 11),),
                                constraints=SupplierConstraint("шт", None, 6)))
        self.assertEqual(row.provisional_quantity, 12)
        self.assertEqual(row.diagnostics["order_multiple_inventory_units"], 6)
        self.assertIsNone(row.diagnostics["min_order_qty_inventory_units"])

    def test_conversion_is_explicit_and_counts_different_units(self):
        row = recommend(request(scenarios=((0, 0, 0, 11),),
                                constraints=SupplierConstraint("коробка", 1, 1, 6)))
        self.assertEqual(row.quantity, 12)
        unknown = recommend(request(constraints=SupplierConstraint("коробка", 1, 1)))
        self.assertEqual(unknown.provisional_quantity, 20)
        self.assertIsNone(unknown.quantity)
        self.assertIn("unknown_constraint:inventory_units_per_order_unit", unknown.warnings)

    def test_decimal_lattice_does_not_add_a_spurious_batch(self):
        for amount, quantum, minimum in ((0.07, 0.01, 0), (0.3, 0.1, 0), (0.3, 0.1, 0.3)):
            with self.subTest(amount=amount, quantum=quantum, minimum=minimum):
                row = recommend(request(scenarios=((0, 0, 0, amount),), unit="кг", unit_quantum=quantum,
                                        constraints=SupplierConstraint("кг", minimum, quantum),
                                        economics=None, service_policy=ServicePolicy(1, "test", "v1")))
                self.assertEqual(row.quantity, amount)
        converted = recommend(request(scenarios=((0, 0, 0, 0.3),), unit="кг", unit_quantum=0.1,
                                      constraints=SupplierConstraint("пакет", 3, 1, 0.1)))
        self.assertEqual(converted.quantity, 0.3)
        summed = recommend(request(scenarios=((0.1, 0.2, 0, 0),), unit="кг", unit_quantum=0.1,
                                   constraints=SupplierConstraint("кг", 0, 0.1),
                                   economics=None, service_policy=ServicePolicy(1, "test", "v1")))
        self.assertEqual(summed.quantity, 0.3)
        stock_difference = recommend(request(scenarios=((0, 0, 0, 0.3),), unit="кг", unit_quantum=0.1,
                                            inventory=InventorySnapshot(AS_OF, 0.3, 0.1),
                                            constraints=SupplierConstraint("кг", 0, 0.1),
                                            economics=None, service_policy=ServicePolicy(1, "test", "v1")))
        self.assertEqual(stock_difference.quantity, 0.1)

    def test_physical_quantum_is_separate_from_supplier_conditions(self):
        req = request(scenarios=((0, 0, 0, 0.1),),
                      constraints=SupplierConstraint("шт", 0.1, 1),
                      economics=None, service_policy=ServicePolicy(1, "test", "v1"))
        self.assertEqual(recommend(req).quantity, 1)
        unknown = recommend(replace(req, constraints=None))
        self.assertEqual(unknown.provisional_quantity, 1)
        self.assertIsNone(unknown.diagnostics["order_multiple_inventory_units"])
        missing = recommend(replace(req, unit_quantum=None))
        self.assertEqual(missing.status, "needs_data")
        self.assertIn("unit_quantum", missing.required_fields)
        with self.assertRaises(ValueError):
            recommend(replace(req, constraints=SupplierConstraint("шт", 0.1, 0.1)))
        with self.assertRaises(ValueError):
            recommend(replace(req, constraints=SupplierConstraint("упак", 1, 1, 0.5)))
        with self.assertRaises(ValueError):
            recommend(replace(req, inventory=InventorySnapshot(AS_OF, 0.5, 0)))
        with self.assertRaises(ValueError):
            recommend(replace(req, inbound=(Inbound("p1", 0.5, AS_OF + timedelta(days=2), "шт", "erp"),)))

    def test_late_inbound_never_hides_early_shortage(self):
        req = request(scenarios=((10, 0, 0, 0),), lead_time_days=2, review_period_days=2,
                      inbound=(Inbound("po", 10, AS_OF + timedelta(days=4), "шт", "erp"),))
        row = recommend(req)
        self.assertEqual(row.quantity, 0)
        self.assertEqual(row.urgency, "expedite")
        self.assertEqual(row.diagnostics["before_arrival_lost_mean"], 10)
        self.assertEqual(row.diagnostics["expected_lost_units"], 10)
        self.assertEqual(row.diagnostics["expected_ending_units"], 10)
        self.assertIn("shortage_before_new_order_arrival", row.warnings)

    def test_inbound_date_changes_post_arrival_quantity(self):
        req = request(scenarios=((0, 10, 0, 0),), lead_time_days=2, review_period_days=2)
        timely = Inbound("po", 10, AS_OF + timedelta(days=2), "шт", "erp")
        late = replace(timely, expected_at=AS_OF + timedelta(days=3))
        self.assertEqual(recommend(replace(req, inbound=(timely,))).quantity, 0)
        self.assertEqual(recommend(replace(req, inbound=(late,))).quantity, 10)
        outside = replace(timely, expected_at=AS_OF + timedelta(days=5))
        row = recommend(replace(req, inbound=(outside,)))
        self.assertEqual(row.quantity, 10)
        self.assertFalse(row.diagnostics["inbound"][0]["in_horizon"])

    def test_external_growth_period_provenance_and_no_double_application(self):
        growth = GrowthAdjustment("campaign", 1, AS_OF + timedelta(days=3), AS_OF + timedelta(days=4), "sales-plan-7")
        req = request(growth_adjustments=(growth,))
        row = recommend(req)
        self.assertEqual(row.quantity, 30)
        self.assertEqual(row.diagnostics["growth"][0]["affected_days"], 2)
        self.assertEqual(row.diagnostics["growth"][0]["source"], "sales-plan-7")
        included = recommend(replace(req, scenarios=((5, 5, 10, 10),), included_growth_ids=("campaign",)))
        self.assertEqual(included.quantity, 30)
        self.assertEqual(included.diagnostics["growth"][0]["action"], "already_in_forecast")
        after = replace(growth, starts_at=AS_OF + timedelta(days=10), ends_at=AS_OF + timedelta(days=11))
        self.assertEqual(recommend(replace(req, growth_adjustments=(after,))).quantity, 20)
        reduction = replace(growth, rate=-1)
        self.assertEqual(recommend(replace(req, growth_adjustments=(reduction,))).quantity, 10)

    def test_material_adds_only_uncovered_disjoint_part(self):
        material = MaterialRequirement("m1", 10, AS_OF + timedelta(days=3), "шт", "1c", 3, 2, "allocation-1")
        row = recommend(request(inventory=InventorySnapshot(AS_OF, 2, 2), materials=(material,)))
        self.assertEqual(row.quantity, 25)
        self.assertEqual(row.diagnostics["materials"][0]["uncovered"], 5)
        self.assertEqual(row.diagnostics["free_stock"], 0)

    def test_forecast_included_hard_obligation_is_still_guaranteed_without_growth(self):
        material = MaterialRequirement("m1", 10, AS_OF + timedelta(days=4), "шт", "1c", 10, 0, "forecast-allocation")
        growth = GrowthAdjustment("g1", 1, AS_OF + timedelta(days=1), AS_OF + timedelta(days=4), "approved")
        row = recommend(request(scenarios=((0, 0, 0, 10),), materials=(material,), growth_adjustments=(growth,),
                                economics=EconomicProfile(0, 1, 4, "KZT", "test")))
        self.assertEqual(row.quantity, 10)
        self.assertEqual(row.diagnostics["regular_demand_mean"], 0)
        self.assertEqual(row.diagnostics["dated_material_demand"], 10)
        self.assertEqual(row.diagnostics["materials"][0]["uncovered"], 0)
        with self.assertRaisesRegex(ValueError, "included_in_forecast exceeds"):
            recommend(request(scenarios=((0, 0, 0, 9),), materials=(material,)))

    def test_hard_material_survives_low_economic_service_level(self):
        material = MaterialRequirement("m1", 10, AS_OF + timedelta(days=3), "шт", "1c")
        req = request(scenarios=((0, 5, 0, 0), (0, 8, 0, 0)), materials=(material,),
                      economics=EconomicProfile(0, 1, 4, "KZT", "test"))
        row = recommend(req)
        # Earlier regular flow consumes the same fungible free stock; supplying
        # 10 alone would not guarantee the day-3 obligation in these scenarios.
        self.assertEqual(row.quantity, 18)
        self.assertEqual(row.diagnostics["hard_material_floor_quantity"], 18)
        self.assertEqual(oracle(req, row.quantity)[2], [0, 0])
        soft = replace(material, hard=False)
        self.assertEqual(recommend(replace(req, materials=(soft,))).quantity, 0)

    def test_hard_material_prioritized_on_same_day_and_prelead_flagged(self):
        material = MaterialRequirement("m1", 4, AS_OF + timedelta(days=1), "шт", "1c")
        req = request(scenarios=((10, 0, 0, 0),), materials=(material,),
                      economics=EconomicProfile(0, 1, 4, "KZT", "test"))
        self.assertEqual(recommend(req).quantity, 4)
        early = recommend(replace(req, lead_time_days=2, review_period_days=2))
        self.assertEqual(early.quantity, 0)
        self.assertEqual(early.diagnostics["before_arrival_material_lost_max"], 4)
        self.assertIn("hard_material_shortage_before_arrival", early.warnings)

    def test_stale_snapshot_forecast_and_overdue_events_request_freshness(self):
        stale = AS_OF - timedelta(days=1)
        row = recommend(request(inventory=InventorySnapshot(stale, 10, 0), forecast_as_of=stale))
        self.assertEqual(row.status, "needs_data")
        self.assertIn("fresh_inventory", row.required_fields)
        self.assertIn("fresh_forecast", row.required_fields)
        self.assertEqual(recommend(request(inventory=InventorySnapshot(stale, 10, 0), max_snapshot_age_days=1)).quantity, 10)
        overdue = Inbound("p1", 10, AS_OF, "шт", "erp")
        self.assertIn("refreshed_eta:p1", recommend(request(inbound=(overdue,))).required_fields)
        material = MaterialRequirement("m1", 1, AS_OF, "шт", "erp")
        self.assertIn("refreshed_material_due_at:m1", recommend(request(materials=(material,))).required_fields)

    def test_invalid_inputs_raise_instead_of_becoming_zero(self):
        bad_inputs = [
            dict(scenarios=((math.nan, 0, 0, 0),)), dict(scenarios=((math.inf, 0, 0, 0),)),
            dict(scenarios=((-1, 0, 0, 0),)), dict(scenarios=()),
            dict(scenarios=((1, 2), (1,))), dict(lead_time_days=-1),
            dict(lead_time_days=True), dict(review_period_days=0),
            dict(lead_time_days=2), dict(unit_cost=math.nan),
            dict(inventory=InventorySnapshot(AS_OF, 10, 2, 10)),
            dict(inventory=InventorySnapshot(AS_OF, 1, 2)),
            dict(inventory=InventorySnapshot(AS_OF, free_stock=-1)),
            dict(inventory=InventorySnapshot(AS_OF + timedelta(days=1), 10, 0)),
            dict(forecast_as_of=AS_OF + timedelta(days=1)),
            dict(constraints=SupplierConstraint("шт", 0, 0)),
            dict(constraints=SupplierConstraint("шт", 0, 1, 2)),
            dict(economics=EconomicProfile(0, 0, 4, "KZT", "test")),
            dict(economics=EconomicProfile(1, 1, 3, "KZT", "test")),
            dict(service_policy=ServicePolicy(1.01, "test", "v1")),
            dict(inbound=(Inbound("p1", 1, AS_OF + timedelta(days=1), "м", "erp"),)),
            dict(materials=(MaterialRequirement("m1", 1, AS_OF + timedelta(days=1), "м", "erp"),)),
            dict(materials=(MaterialRequirement("m1", 1, AS_OF + timedelta(days=1), "шт", "erp", 1, 1, "accounting"),)),
            dict(materials=(MaterialRequirement("m1", 1, AS_OF + timedelta(days=1), "шт", "erp", 1),)),
            dict(materials=(MaterialRequirement("m1", 1, AS_OF + timedelta(days=1), "шт", "erp", 0, 1, "allocation"),)),
            dict(growth_adjustments=(GrowthAdjustment("g1", -1.01, AS_OF, AS_OF, "test"),)),
            dict(growth_adjustments=(GrowthAdjustment("g1", 1, AS_OF + timedelta(days=1), AS_OF, "test"),)),
        ]
        for change in bad_inputs:
            with self.subTest(change=change), self.assertRaises(ValueError):
                recommend(request(**change))

    def test_duplicate_source_ids_cannot_double_count(self):
        inbound = Inbound("p1", 1, AS_OF + timedelta(days=1), "шт", "erp")
        material = MaterialRequirement("m1", 1, AS_OF + timedelta(days=1), "шт", "erp")
        growth = GrowthAdjustment("g1", 0.2, AS_OF, AS_OF + timedelta(days=2), "erp")
        for change in (dict(inbound=(inbound, inbound)), dict(materials=(material, material)),
                       dict(growth_adjustments=(growth, growth)), dict(included_growth_ids=("g1", "g1"))):
            with self.subTest(change=change), self.assertRaises(ValueError):
                recommend(request(**change))

    def test_grouping_every_row_has_reason_including_needs_data(self):
        rows = [recommend(request()), recommend(request(sku="002", supplier_id="S2")),
                recommend(request(sku="003", inventory=None))]
        grouped = group_by_supplier(rows)
        self.assertEqual(set(grouped), {"S1", "S2"})
        self.assertEqual(len(grouped["S1"]), 2)
        self.assertTrue(all(row.reason for group in grouped.values() for row in group))
        with self.assertRaises(ValueError):
            group_by_supplier([rows[0], rows[0]])

    def test_budget_revision_and_human_confirmation_are_independent_gates(self):
        row = recommend(request())
        kwargs = dict(current_revision=2, expected_revision=2, confirmed_by="manager", budget=200, currency="KZT")
        self.assertTrue(check_approval([row], **kwargs).allowed)
        self.assertEqual(check_approval([row], **kwargs).total_cost, 200)
        self.assertIn("budget_exceeded", check_approval([row], **(kwargs | {"budget": 199})).reasons)
        self.assertIn("revision_conflict", check_approval([row], **(kwargs | {"expected_revision": 1})).reasons)
        self.assertIn("human_confirmation_required", check_approval([row], **(kwargs | {"confirmed_by": None})).reasons)
        unknown_price = recommend(request(unit_cost=None))
        self.assertIsNone(check_approval([unknown_price], **kwargs).total_cost)
        self.assertFalse(check_approval([unknown_price], **kwargs).allowed)
        self.assertTrue(check_approval([unknown_price], **(kwargs | {"budget": None})).allowed)
        foreign_price = recommend(request(price_currency="USD"))
        self.assertFalse(check_approval([foreign_price], **kwargs).allowed)

    def test_decimal_budget_avoids_binary_float_false_exceedance(self):
        row = recommend(request(scenarios=((0, 0, 0, 0.1),), unit="кг", unit_quantum=0.1,
                                constraints=SupplierConstraint("кг", 0, 0.1), unit_cost=3))
        verdict = check_approval([row], current_revision=1, expected_revision=1,
                                 confirmed_by="manager", budget=0.3, currency="KZT")
        self.assertTrue(verdict.allowed)
        self.assertEqual(verdict.total_cost, 0.3)

    def test_approval_rechecks_physical_lot_and_hard_floor(self):
        req = request(constraints=SupplierConstraint("шт", 4, 2))
        row = recommend(req)
        kwargs = dict(current_revision=1, expected_revision=1, confirmed_by="manager")
        self.assertFalse(check_approval([replace(row, quantity=0.5)], **kwargs).allowed)
        self.assertFalse(check_approval([replace(row, quantity=3)], **kwargs).allowed)
        material = MaterialRequirement("m1", 10, AS_OF + timedelta(days=4), "шт", "1c")
        hard_row = recommend(replace(req, materials=(material,)))
        self.assertFalse(check_approval([replace(hard_row, quantity=0)], **kwargs).allowed)

    def test_review_needs_reason_but_cannot_override_missing_data(self):
        row = recommend(request(category_id="unknown"))
        kwargs = dict(current_revision=1, expected_revision=1, confirmed_by="manager")
        self.assertFalse(check_approval([row], **kwargs).allowed)
        review = {("S1", "ALM", "001"): "Явный профиль SKU подтверждён"}
        self.assertTrue(check_approval([row], review_reasons=review, **kwargs).allowed)
        missing = recommend(request(inventory=None))
        self.assertFalse(check_approval([missing], review_reasons=review, **kwargs).allowed)

    def test_empty_order_does_not_approve(self):
        self.assertFalse(check_approval([], current_revision=0, expected_revision=0,
                                        confirmed_by="manager").allowed)


class PropertyCases(unittest.TestCase):
    def test_400_random_cases_match_independent_exhaustive_inventory_oracle(self):
        rng = random.Random(63211)
        for case in range(400):
            horizon = rng.randint(2, 7)
            lead = rng.randrange(horizon)
            scenarios = [tuple(rng.randint(0, 8) for _ in range(horizon))
                         for _ in range(rng.randint(1, 6))]
            inbound = tuple(Inbound(f"p{i}", rng.randint(0, 12), AS_OF + timedelta(days=rng.randint(1, horizon)), "шт", "oracle")
                            for i in range(rng.randint(0, 3)))
            materials = tuple(MaterialRequirement(f"m{i}", rng.randint(0, 6), AS_OF + timedelta(days=rng.randint(1, horizon)),
                                                 "шт", "oracle", hard=bool(rng.randrange(2)))
                              for i in range(rng.randint(0, 2)))
            multiple, minimum = rng.randint(1, 5), rng.randint(0, 14)
            under, over = rng.randint(0, 9), rng.randint(1, 9)
            req = request(scenarios=scenarios, lead_time_days=lead, review_period_days=horizon-lead,
                          inbound=inbound, materials=materials,
                          inventory=InventorySnapshot(AS_OF, rng.randint(0, 20), 0),
                          constraints=SupplierConstraint("шт", minimum, multiple),
                          economics=EconomicProfile(under, over, horizon, "KZT", "oracle"))
            row = recommend(req)
            # A physical upper bound comes from all possible demand; the
            # production optimizer itself does not use this enumeration bound.
            bound = max(sum(s) for s in scenarios) + sum(m.quantity for m in materials) + minimum + multiple
            feasible = [0] + [q for q in range(multiple, int(bound) + multiple + 1, multiple) if q >= minimum]
            scores = []
            for q in feasible:
                loss, ending, hard_loss = oracle(req, q)
                if max(hard_loss) > 0:
                    continue
                scores.append((sum(under*x + over*y for x, y in zip(loss, ending))/len(scenarios), q))
            expected_cost, expected_quantity = min(scores)
            with self.subTest(case=case):
                self.assertEqual(row.quantity, expected_quantity)
                self.assertAlmostEqual(row.diagnostics["expected_loss_cost"], expected_cost, places=9)
                actual_loss, actual_ending, actual_hard_loss = oracle(req, row.quantity)
                self.assertAlmostEqual(row.diagnostics["expected_lost_units"], sum(actual_loss)/len(scenarios), places=9)
                self.assertAlmostEqual(row.diagnostics["expected_ending_units"], sum(actual_ending)/len(scenarios), places=9)
                self.assertEqual(max(actual_hard_loss), 0)

    def test_150_random_monotonic_stock_inbound_economics_and_service_cases(self):
        rng = random.Random(1251)
        for case in range(150):
            scenarios = [tuple(rng.randint(0, 12) for _ in range(4)) for _ in range(5)]
            stock = rng.randint(0, 10)
            req = request(scenarios=scenarios, inventory=InventorySnapshot(AS_OF, stock, 0),
                          economics=EconomicProfile(3, 3, 4, "KZT", "oracle"))
            row = recommend(req)
            with self.subTest(case=case):
                self.assertLessEqual(recommend(replace(req, inventory=InventorySnapshot(AS_OF, stock+5, 0))).quantity, row.quantity)
                timely = Inbound("p1", 5, AS_OF + timedelta(days=1), "шт", "oracle")
                self.assertLessEqual(recommend(replace(req, inbound=(timely,))).quantity, row.quantity)
                self.assertGreaterEqual(recommend(replace(req, economics=EconomicProfile(9, 3, 4, "KZT", "oracle"))).quantity, row.quantity)
                self.assertLessEqual(recommend(replace(req, economics=EconomicProfile(3, 9, 4, "KZT", "oracle"))).quantity, row.quantity)
                low = replace(req, economics=None, service_policy=ServicePolicy(.4, "test", "v1"))
                high = replace(low, service_policy=ServicePolicy(.9, "test", "v1"))
                self.assertGreaterEqual(recommend(high).quantity, recommend(low).quantity)


if __name__ == "__main__":
    unittest.main()
