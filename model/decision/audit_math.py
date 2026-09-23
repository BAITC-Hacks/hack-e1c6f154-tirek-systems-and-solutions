"""Reproduce independent, bounded checks of decision arithmetic (stdlib only).

Run from repository root:
    python -m model.decision.audit_math --output model/decision/math_audit.json

The day-by-day Decimal oracle inserts each candidate order into inventory flow;
it does not use the core's reduction to post-arrival demand or its optimizer.
The optimizer oracle enumerates every relevant feasible lot and computes exact
Decimal losses. The rank oracle checks every integer percentile for 1..100 paths.
All inputs are synthetic. Functional success is not forecast accuracy.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_CEILING
import hashlib
import json
from pathlib import Path
import platform
import random
import sys
from time import perf_counter

from model.decision import core

D = Decimal
AS_OF = date(2026, 1, 1)
ALLOCATION_SEED = 853071
OPTIMIZER_SEED = 9361
ALLOCATION_CASES = 300
OPTIMIZER_CASES = 5000


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _request(**changes):
    defaults = dict(
        sku="synthetic-001", warehouse_id="synthetic-warehouse", supplier_id="synthetic-supplier",
        unit="шт", as_of=AS_OF, forecast_as_of=AS_OF, forecast_id="math-audit-synthetic-v1",
        unit_quantum=1, scenarios=((5, 5, 5, 5),), lead_time_days=1, review_period_days=3,
        inventory=core.InventorySnapshot(AS_OF, on_hand=0, reserved=0, source="synthetic"),
        constraints=core.SupplierConstraint("шт", min_order_qty=0, order_multiple=1, source="synthetic"),
        economics=core.EconomicProfile(9, 1, 4, "KZT", "synthetic"),
        data_version="math-audit-synthetic-v1",
    )
    defaults.update(changes)
    return core.RecommendationInput(**defaults)


def _flow(request, quantity: Decimal):
    """Sequential dispatch oracle, with explicit Q and independent Decimal state."""
    result = []
    inventory = request.inventory
    free = (D(str(inventory.free_stock)) if inventory.free_stock is not None
            else D(str(inventory.on_hand)) - D(str(inventory.reserved)))
    for path in request.scenarios:
        stock = free
        if request.lead_time_days == 0:
            stock += quantity
        lost = hard_loss_after_arrival = loss_after_arrival = D(0)
        for index, observed_demand in enumerate(path, 1):
            day = request.as_of + timedelta(days=index)
            if index == request.lead_time_days:
                stock += quantity
            stock += sum((D(str(item.quantity)) for item in request.inbound if item.expected_at == day), D(0))
            materials = [item for item in request.materials if item.due_at == day]
            hard = sum((D(str(item.quantity)) - D(str(item.already_reserved))
                        for item in materials if item.hard), D(0))
            served_hard = min(hard, stock)
            stock -= served_hard
            hard_lost = hard - served_hard
            lost += hard_lost
            if index >= request.lead_time_days:
                hard_loss_after_arrival += hard_lost
                loss_after_arrival += hard_lost
            regular = D(str(observed_demand)) - sum((D(str(item.included_in_forecast)) for item in materials), D(0))
            for adjustment in request.growth_adjustments:
                if (adjustment.starts_at <= day <= adjustment.ends_at
                        and adjustment.adjustment_id not in request.included_growth_ids):
                    regular *= 1 + D(str(adjustment.rate))
            soft = sum((D(str(item.quantity)) - D(str(item.already_reserved))
                        for item in materials if not item.hard), D(0))
            demand = regular + soft
            served_other = min(demand, stock)
            stock -= served_other
            other_lost = demand - served_other
            lost += other_lost
            if index >= request.lead_time_days:
                loss_after_arrival += other_lost
        result.append((lost, stock, hard_loss_after_arrival, loss_after_arrival))
    return result


def _group(name, count, seed, domains):
    return {"name": name, "cases": count, "seed": seed, "domains": domains,
            "passed": 0, "failed": 0, "failure_examples": []}


def _record(group, case_id, issues):
    if not issues:
        group["passed"] += 1
    else:
        group["failed"] += 1
        if len(group["failure_examples"]) < 20:
            group["failure_examples"].append({"case_id": case_id, "issues": issues})


def audit_allocations():
    started = perf_counter()
    rng = random.Random(ALLOCATION_SEED)
    group = _group("sequential_decimal_inventory_oracle", ALLOCATION_CASES, ALLOCATION_SEED, {
        "horizon_days": 4, "lead_days": [0, 1, 2, 3], "scenario_count": [1, 5],
        "regular_daily_demand_integer_range": [0, 5], "material_count": [0, 2],
        "material_forecast_allocation_integer_range": [0, 3], "material_reserve_integer_range": [0, 2],
        "material_uncovered_integer_range": [0, 3], "free_stock_integer_range": [0, 8],
        "growth_rates": [-0.2, 0, 0.2, 1], "service_levels": [0, 0.5, 1],
        "order_multiple_integer_range": [1, 5], "minimum_order_integer_range": [0, 6],
        "underage_cost_integer_range": [0, 9], "overage_cost_integer_range": [1, 9],
        "existing_inbound": "none in these 300 cases; dated insertion of the new order is tested",
        "exhaustive_quantity_bound": "0 plus positive lot multiples below 120; maximum total adjusted demand is 52, MOQ<=6, multiple<=5",
    })
    for case in range(ALLOCATION_CASES):
        count, horizon, lead = rng.randint(1, 5), 4, rng.randrange(4)
        materials, reserved = [], 0
        for index in range(rng.randrange(3)):
            allocation, reservation = rng.randrange(4), rng.randrange(3)
            quantity = allocation + reservation + rng.randrange(4)
            reserved += reservation
            materials.append(core.MaterialRequirement(
                "material-" + str(index), quantity, AS_OF + timedelta(days=rng.randint(1, horizon)),
                "шт", "synthetic", allocation, reservation, "synthetic-disjoint-allocation", bool(rng.randrange(2))))
        scenarios = []
        for _ in range(count):
            scenarios.append(tuple(rng.randrange(6) + sum(item.included_in_forecast for item in materials
                                   if (item.due_at - AS_OF).days == day) for day in range(1, horizon + 1)))
        minimum, multiple = rng.randrange(7), rng.randint(1, 5)
        under, over = rng.randint(0, 9), rng.randint(1, 9)
        service = rng.choice((0, 0.5, 1))
        growth = core.GrowthAdjustment("growth", rng.choice((-0.2, 0, 0.2, 1)),
                                      AS_OF + timedelta(days=1), AS_OF + timedelta(days=4), "synthetic")
        request = _request(
            scenarios=scenarios, lead_time_days=lead, review_period_days=horizon - lead,
            materials=tuple(materials), inventory=core.InventorySnapshot(AS_OF, reserved + rng.randrange(9), reserved),
            constraints=core.SupplierConstraint("шт", minimum, multiple),
            economics=core.EconomicProfile(under, over, horizon, "KZT", "synthetic"),
            growth_adjustments=(growth,), service_policy=core.ServicePolicy(service, "synthetic", "1"))
        issues = []
        try:
            recommendation = core.recommend(request)
            candidates = []
            for quantity in [0] + list(range(multiple, 120, multiple)):
                if quantity and quantity < minimum:
                    continue
                flows = _flow(request, D(quantity))
                if any(row[2] > 0 for row in flows):
                    continue
                if sum(row[3] == 0 for row in flows) < D(str(service)) * count:
                    continue
                loss = sum(under * row[0] + over * row[1] for row in flows) / count
                candidates.append((loss, quantity))
            expected_loss, expected_quantity = min(candidates)
            if recommendation.quantity != expected_quantity:
                issues.append({"field": "quantity", "actual": recommendation.quantity, "expected": expected_quantity})
            actual_flows = _flow(request, D(str(recommendation.quantity)))
            if any(row[2] > 0 for row in actual_flows):
                issues.append({"field": "hard_material", "issue": "selected order leaves a post-arrival hard obligation uncovered"})
            expected_diagnostics = {
                "expected_loss_cost": expected_loss,
                "expected_lost_units": sum(row[0] for row in actual_flows) / count,
                "expected_ending_units": sum(row[1] for row in actual_flows) / count,
            }
            for field, expected in expected_diagnostics.items():
                actual = D(str(recommendation.diagnostics[field]))
                if abs(actual - expected) > D("1e-9"):
                    issues.append({"field": field, "actual": str(actual), "expected": str(expected)})
            if issues:
                issues.append({"request": asdict(request)})
        except Exception as error:
            issues.append({"exception": type(error).__name__, "message": str(error), "request": asdict(request)})
        _record(group, case, issues)
    group["runtime_seconds"] = round(perf_counter() - started, 6)
    return group


def audit_optimizer():
    started = perf_counter()
    rng = random.Random(OPTIMIZER_SEED)
    group = _group("exact_decimal_discrete_optimizer_oracle", OPTIMIZER_CASES, OPTIMIZER_SEED, {
        "scenario_count": [1, 7], "requirement_decimal_range": ["0.0", "2.9"], "requirement_step": "0.1",
        "underage_and_overage_cost_range": ["0.1", "0.9"], "cost_step": "0.1",
        "lot_multiple_range": ["0.1", "0.5"], "lot_step": "0.1", "minimum_order_range": ["0.0", "0.9"],
        "floor_quantity": 0, "tie_break": "smallest quantity among exactly equal Decimal losses",
        "exhaustive_bound": "0 plus the first 49 lot multiples; even at the smallest lot, 4.9 exceeds every requirement and MOQ",
    })
    for case in range(OPTIMIZER_CASES):
        requirements = [D(rng.randrange(30)) / 10 for _ in range(rng.randrange(1, 8))]
        under, over = D(rng.randrange(1, 10)) / 10, D(rng.randrange(1, 10)) / 10
        multiple, minimum = D(rng.randrange(1, 6)) / 10, D(rng.randrange(10)) / 10
        issues = []
        try:
            actual = core._choose_quantity([float(value) for value in requirements], 0, float(minimum), float(multiple),
                                           core.EconomicProfile(float(under), float(over), 1, "KZT", "synthetic"))
            scores = []
            for quantity in [D(0)] + [multiple * index for index in range(1, 50) if multiple * index >= minimum]:
                loss = sum(under * max(value - quantity, D(0)) + over * max(quantity - value, D(0)) for value in requirements)
                scores.append((loss, quantity))
            expected_loss, expected_quantity = min(scores)
            if D(str(actual)) != expected_quantity:
                issues.append({"actual": actual, "expected": str(expected_quantity), "expected_loss_sum": str(expected_loss),
                               "requirements": [str(value) for value in requirements], "under": str(under), "over": str(over),
                               "multiple": str(multiple), "minimum": str(minimum)})
        except Exception as error:
            issues.append({"exception": type(error).__name__, "message": str(error)})
        _record(group, case, issues)
    group["runtime_seconds"] = round(perf_counter() - started, 6)
    return group


def audit_quantiles():
    started = perf_counter()
    group = _group("exact_percentile_rank_oracle", 10000, None, {
        "enumeration": "all 100 scenario counts times all 100 integer percentiles",
        "scenario_count": [1, 100], "percentile": [1, 100], "values": "1..scenario_count",
        "expected_rank": "Decimal ceil(percentile * scenario_count / 100)",
    })
    for count in range(1, 101):
        for percentile in range(1, 101):
            issues = []
            try:
                probability = D(percentile) / 100
                expected = int((probability * count).to_integral_value(rounding=ROUND_CEILING))
                actual = core._quantile(list(range(1, count + 1)), float(probability))
                if actual != expected:
                    issues.append({"scenario_count": count, "percentile": percentile, "actual": actual, "expected": expected})
            except Exception as error:
                issues.append({"exception": type(error).__name__, "message": str(error)})
            _record(group, f"{count}:{percentile}", issues)
    group["runtime_seconds"] = round(perf_counter() - started, 6)
    return group


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(__file__).with_name("math_audit.json"))
    args = parser.parse_args()
    started = perf_counter()
    core_path, script_path = Path(core.__file__), Path(__file__)
    core_before, script_before = _sha256(core_path), _sha256(script_path)
    groups = [audit_allocations(), audit_optimizer(), audit_quantiles()]
    core_after, script_after = _sha256(core_path), _sha256(script_path)
    changed = core_before != core_after or script_before != script_after
    failure_count = sum(group["failed"] for group in groups)
    report = {
        "audit_version": "1.0", "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "command": "python -m model.decision.audit_math --output model/decision/math_audit.json",
        "python_version": platform.python_version(), "decimal_precision": 28,
        "core_path": "model/decision/core.py", "core_sha256": core_before,
        "core_sha256_after_run": core_after, "audit_script_sha256": script_before,
        "source_changed_during_run": changed,
        "cases": sum(group["cases"] for group in groups), "passed": sum(group["passed"] for group in groups),
        "failed": failure_count, "status": "passed" if failure_count == 0 and not changed else "failed",
        "runtime_seconds": round(perf_counter() - started, 6), "groups": groups,
        "failure_reporting": "All cases run and contribute to counts; at most 20 failure examples are stored per group.",
        "limits": [
            "This is a synthetic arithmetic audit, not a measurement of forecast accuracy or real-world service levels.",
            "Random seeds and bounded domains are fixed; success is not an exhaustive proof over arbitrary inputs.",
            "The 300 inventory cases cover the new order's dated arrival; existing inbound is covered by the separate core test suite.",
            "The optimizer and quantile groups exercise private numeric helpers, independently of input validation.",
            "No claim about floating-point noise introduced by a forecasting model or integration adapter is made by this audit.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "cases": report["cases"], "passed": report["passed"],
                      "failed": report["failed"], "runtime_seconds": report["runtime_seconds"],
                      "core_sha256": core_before, "report": str(args.output)}, ensure_ascii=False))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
