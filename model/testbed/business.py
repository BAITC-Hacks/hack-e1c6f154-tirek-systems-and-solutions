"""Independent fixture checker and illustrative calculator, not the production engine.

Expected answers live in fixtures/business_cases.json and are never computed here.
Dates: receipt at start of day, demand at end; day 1 is the day after as_of.
"""
from datetime import date, timedelta
from decimal import Decimal, ROUND_CEILING
import json
import math
from pathlib import Path


def round_up(value, step):
    value, step = Decimal(str(value)), Decimal(str(step))
    return float((value / step).to_integral_value(rounding=ROUND_CEILING) * step)


def reference_calculate(request):
    """Small contract example to make hand-authored integration fixtures executable."""
    horizon = request["horizon_days"]
    cutoff = date.fromisoformat(request["as_of"])
    items = []
    for p in request["items"]:
        result = {"sku": p["sku"], "supplier_id": p["supplier_id"], "quantity": None,
                  "status": "ok", "rules": [], "missing": [], "reason": "Synthetic reference calculation"}
        items.append(result)
        missing = result["missing"]
        if p.get("free_stock") is None and p.get("on_hand") is None:
            missing.append("stock")
        if not p.get("stock_current", True):
            missing.append("current_stock")
        if p.get("free_stock") is None and p.get("reserved") is None:
            missing.append("reserved")
        if p.get("lead_time_days") is None:
            missing.append("lead_time_days")
        policy = request["category_policies"].get(p["category_raw"])
        economics = p.get("economics")
        if policy is None and economics is None:
            missing.append("policy_or_economics")
        if p.get("lead_time_days") is not None and p["lead_time_days"] + p["review_period_days"] != horizon:
            missing.append("horizon_equals_lead_plus_review")
        if economics and economics["horizon_days"] != horizon:
            missing.append("economics_horizon")
        if not p.get("daily_mean") or len(p["daily_mean"]) != horizon or any(v is None for v in p["daily_mean"]):
            missing.append("daily_mean")
        if p.get("unit_quantum") is None or p["unit_quantum"] <= 0:
            missing.append("unit_quantum")
        if any(m.get("unit", p["unit"]) != p["unit"] for m in p.get("material_requirements", [])):
            missing.append("material_unit_conversion")
        if any(i.get("unit", p["unit"]) != p["unit"] for i in p.get("inbound", [])):
            missing.append("inbound_unit_conversion")
        multiplier = p.get("order_to_base_factor")
        if p["order_unit"] == p["unit"]:
            multiplier = 1
        elif multiplier is None or multiplier <= 0:
            missing.append("unit_conversion")
        if policy and (not 0 < policy["target_quantile"] < 1 or
                       policy["target_quantile"] < (policy.get("minimum_target_quantile") or 0)):
            missing.append("valid_category_policy")
        if economics and (economics["underage_cost"] < 0 or economics["overage_cost"] < 0 or
                          economics["underage_cost"] + economics["overage_cost"] == 0):
            missing.append("valid_economics")
        distribution = p.get("horizon_distribution", [])
        if (not distribution or any(v["quantity"] < 0 or v["probability"] < 0 for v in distribution)
                or not math.isclose(sum(v["probability"] for v in distribution), 1)):
            missing.append("horizon_distribution")
        if missing:
            result.update(status="needs_data", rules=["DATA-01"])
            continue
        q = economics["underage_cost"] / (economics["underage_cost"] + economics["overage_cost"]) if economics else policy["target_quantile"]
        q = max(q, (policy.get("minimum_target_quantile") or 0) if policy else 0)
        result["rules"].append("POLICY-01" if economics else "POLICY-02")
        running = 0.0
        target = 0.0
        for value in sorted(distribution, key=lambda v: v["quantity"]):
            running += value["probability"]
            if running + 1e-12 >= q:
                target = value["quantity"]
                break
        daily = list(p["daily_mean"])
        original_mean = sum(daily)
        applied = set(p.get("growth_already_included", []))
        for growth in p.get("growth_adjustments", []):
            if growth["id"] in applied:
                continue
            for d in range(horizon):
                stamp = (cutoff + timedelta(days=d + 1)).isoformat()
                if growth["valid_from"] <= stamp <= growth["valid_to"]:
                    daily[d] *= 1 + growth["rate"]
            applied.add(growth["id"])
            result["rules"].append("DEMAND-04")
        if original_mean:
            target *= sum(daily) / original_mean
        free = p["free_stock"] if p.get("free_stock") is not None else p["on_hand"] - p["reserved"]
        commitments = [m for m in p.get("material_requirements", [])
                       if m["needed_at"] <= (cutoff + timedelta(days=horizon)).isoformat()]
        uncovered = sum(max(0, m["quantity"] - m["already_accounted_quantity"]) for m in commitments)
        inbound = [i for i in p.get("inbound", []) if i["status"] == "confirmed"
                   and request["as_of"] < i["expected_at"] <= (cutoff + timedelta(days=horizon)).isoformat()]
        inbound_qty = sum(i["quantity"] for i in inbound)
        need = max(0, target + uncovered - free - inbound_qty)
        moq, multiple = p.get("min_order_qty"), p.get("order_multiple")
        if moq is None or multiple is None:
            quantity = round_up(need, p["unit_quantum"])
            result.update(status="needs_review")
            result["rules"].append("SUPPLY-04")
        else:
            moq, multiple = moq * multiplier, multiple * multiplier
            if moq < 0 or multiple <= 0 or not math.isclose(multiple / p["unit_quantum"], round(multiple / p["unit_quantum"])):
                result.update(status="needs_data", missing=["valid_order_constraints"], rules=["DATA-01"])
                continue
            quantity = round_up(max(need, moq), multiple) if need > 0 else 0
            result["rules"].append("SUPPLY-03")
        # Timeline deliberately excludes the proposed ordinary order to identify the gap.
        balance, first_deficit, early_shortfall = free, None, 0.0
        for d, demand in enumerate(daily, 1):
            stamp = (cutoff + timedelta(days=d)).isoformat()
            balance += sum(i["quantity"] for i in inbound if i["expected_at"] == stamp)
            balance -= demand
            balance -= sum(max(0, m["quantity"] - m["already_accounted_quantity"])
                           for m in commitments if m["needed_at"] == stamp or (d == 1 and m["needed_at"] < stamp))
            if balance < -1e-9:
                first_deficit = first_deficit or d
                if d < p["lead_time_days"]:
                    early_shortfall = max(early_shortfall, -balance)
        if early_shortfall:
            result["rules"].append("SUPPLY-02")
        if p.get("reserved", 0) or commitments:
            result["rules"].append("SUPPLY-01")
        result.update(quantity=quantity, forecast_mean=sum(daily), target_stock=target,
                      target_quantile=q, free_stock=free, material_uncovered=uncovered,
                      inbound_in_horizon=inbound_qty, first_deficit_day=first_deficit,
                      early_shortfall=early_shortfall, requires_expedite=early_shortfall > 0,
                      economics_source="synthetic-profile" if economics else None,
                      unit=p["unit"], unit_cost=p.get("unit_cost"))
    known_total = sum(i["quantity"] * i["unit_cost"] for i in items
                      if i["quantity"] is not None and i.get("unit_cost") is not None)
    unknown_price = any(i["quantity"] is not None and i["quantity"] > 0 and i.get("unit_cost") is None for i in items)
    budget = request.get("budget_kzt")
    rules = []
    if budget is not None and unknown_price:
        rules.append("BUDGET-01")
    if budget is not None and known_total > budget:
        rules.append("BUDGET-02")
    incomplete = any(i["quantity"] is None for i in items)
    return {"items": items, "known_cost_kzt": known_total, "total_cost_kzt": None if unknown_price or incomplete else known_total,
            "budget_excess_kzt": max(0, known_total - budget) if budget is not None else None,
            "approval_blocked": bool(rules) or any(i["status"] != "ok" for i in items),
            "rules": rules, "supplier_groups": {supplier: [i["sku"] for i in items if i["supplier_id"] == supplier]
                                                for supplier in sorted({i["supplier_id"] for i in items})}}


def compare(expected, actual, path="result"):
    """Subset comparison for objects; exact lists and tolerant finite numbers."""
    failures = []
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return [f"{path}: expected object, got {actual!r}"]
        for key, value in expected.items():
            failures += ([f"{path}.{key}: missing"] if key not in actual else compare(value, actual[key], f"{path}.{key}"))
    elif isinstance(expected, list):
        if not isinstance(actual, list) or len(expected) != len(actual):
            return [f"{path}: list length/type differs"]
        for index, value in enumerate(expected):
            failures += compare(value, actual[index], f"{path}[{index}]")
    elif isinstance(expected, (int, float)) and not isinstance(expected, bool):
        if isinstance(actual, bool) or not isinstance(actual, (int, float)) or not math.isfinite(actual) or not math.isclose(expected, actual, rel_tol=1e-9, abs_tol=1e-8):
            failures.append(f"{path}: expected {expected!r}, got {actual!r}")
    elif type(expected) is not type(actual) or expected != actual:
        failures.append(f"{path}: expected {expected!r}, got {actual!r}")
    return failures


def check_cases(calculator):
    fixture = json.loads(Path(__file__).with_name("fixtures").joinpath("business_cases.json").read_text())
    rows = []
    for case in fixture["cases"]:
        try:
            actual = calculator(case["input"])
            failures = compare(case["expected"], actual)
            for assertion in case.get("contains", []):
                value = actual
                for part in assertion["path"]:
                    value = value[part]
                if assertion["value"] not in value:
                    failures.append(f"Missing {assertion['value']} at {assertion['path']}")
            for row in actual.get("items", []):
                if not row.get("reason") or not row.get("rules"):
                    failures.append(f"{row.get('sku')}: explanation/rules missing")
        except Exception as exc:
            actual, failures = None, [f"{type(exc).__name__}: {exc}"]
        rows.append({"id": case["id"], "requirements": case["requirements"], "passed": not failures,
                     "failures": failures, "actual": actual})
    return rows
