"""Fixture checker and illustrative procurement calculator, not production.

Receipts occur at day start, demand at day end; day1 follows as_of. Quantities
with unknown MOQ/multiple are explicitly provisional base needs, not valid lots.
All arithmetic stays Decimal until the finite JSON response is constructed.
"""
from datetime import date, timedelta
from copy import deepcopy
from decimal import Decimal, ROUND_CEILING, localcontext, DecimalException
import json
import math
from pathlib import Path


ZERO = Decimal(0)
ONE = Decimal(1)
PROBABILITY_TOLERANCE = Decimal("1e-9")
RULE_IDS = {"DATA-01", "DATA-02", "DEMAND-01", "DEMAND-02", "DEMAND-03", "DEMAND-04",
            "SUPPLY-01", "SUPPLY-02", "SUPPLY-03", "SUPPLY-04", "POLICY-01", "POLICY-02",
            "BUDGET-01", "BUDGET-02", "APPROVAL-01", "AI-01"}


def decimal_number(value, *, nonnegative=True):
    """JSON numbers only; bool and nonfinite/negative domain values are invalid."""
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ValueError("expected a finite number")
    number = Decimal(str(value))
    if not number.is_finite() or (nonnegative and number < 0):
        raise ValueError("expected a finite nonnegative number")
    return number


def strict_date(value):
    if not isinstance(value, str):
        raise ValueError("expected an ISO date")
    stamp = date.fromisoformat(value)
    if stamp.isoformat() != value:
        raise ValueError("expected YYYY-MM-DD")
    return stamp


def _integer(value, minimum=0):
    return type(value) is int and value >= minimum


def _number_valid(value, *, positive=False, nonnegative=True):
    try:
        number = decimal_number(value, nonnegative=nonnegative)
        return not positive or number > 0
    except (ValueError, DecimalException):
        return False


def _date_valid(value):
    try:
        strict_date(value)
        return True
    except (ValueError, TypeError, OverflowError):
        return False


def _round_decimal(value, step):
    value, step = decimal_number(value), decimal_number(step)
    if step <= 0:
        raise ValueError("step must be positive")
    return (value / step).to_integral_value(rounding=ROUND_CEILING) * step


def round_up(value, step):
    with localcontext() as context:
        context.prec = 80
        return _json_numbers(_round_decimal(value, step))


def _json_numbers(value):
    if isinstance(value, Decimal):
        result = float(value)
        if not math.isfinite(result):
            raise ValueError("computed value exceeds finite JSON numeric range")
        return result
    if isinstance(value, dict):
        return {key: _json_numbers(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_numbers(item) for item in value]
    return value


def _display(value):
    """Human explanation only: six significant digits, without changing math."""
    if value is None:
        return "unknown"
    mantissa, separator, exponent = format(value, ".6g").partition("e")
    if "." in mantissa:
        mantissa = mantissa.rstrip("0").rstrip(".")
    return mantissa + (separator + exponent if separator else "")


def _validate_item(p, request):
    horizon = request["horizon_days"]
    missing = []
    def add(field):
        if field not in missing:
            missing.append(field)

    if p.get("free_stock") is None and p.get("on_hand") is None:
        add("stock")
    if p.get("stock_current") is not True:
        add("current_stock")
    if p.get("free_stock") is None and p.get("reserved") is None:
        add("reserved")
    for key in ("on_hand", "reserved", "free_stock"):
        if p.get(key) is not None and not _number_valid(p[key]):
            add("valid_" + key)
    if p.get("lead_time_days") is None:
        add("lead_time_days")
    elif not _integer(p["lead_time_days"]):
        add("valid_lead_time_days")
    if not _integer(p.get("review_period_days")):
        add("valid_review_period_days")
    if _integer(p.get("lead_time_days")) and _integer(p.get("review_period_days")) and p["lead_time_days"] + p["review_period_days"] != horizon:
        add("horizon_equals_lead_plus_review")

    policy = request["category_policies"].get(p.get("category_raw"))
    economics = p.get("economics")
    if policy is None and economics is None:
        add("policy_or_economics")
    if policy is not None:
        valid = isinstance(policy, dict) and _number_valid(policy.get("target_quantile"))
        if valid:
            q = decimal_number(policy["target_quantile"])
            minimum = policy.get("minimum_target_quantile")
            valid = 0 < q < 1 and (minimum is None or (_number_valid(minimum) and decimal_number(minimum) <= q))
        if not valid:
            add("valid_category_policy")
    if economics is not None:
        if not isinstance(economics, dict):
            add("valid_economics")
        else:
            if not _integer(economics.get("horizon_days"), 1) or economics["horizon_days"] != horizon:
                add("economics_horizon")
            if not all(_number_valid(economics.get(k)) for k in ("underage_cost", "overage_cost")):
                add("valid_economics")
            elif decimal_number(economics["underage_cost"]) + decimal_number(economics["overage_cost"]) == 0:
                add("valid_economics")
            if not isinstance(economics.get("source"), str) or not economics["source"].strip():
                add("economics_source")

    daily = p.get("daily_mean")
    if not isinstance(daily, list) or len(daily) != horizon or not all(_number_valid(v) for v in daily):
        add("daily_mean")
    if not _number_valid(p.get("unit_quantum"), positive=True):
        add("unit_quantum")
    if not isinstance(p.get("unit"), str) or not p["unit"].strip() or not isinstance(p.get("order_unit"), str) or not p["order_unit"].strip():
        add("unit")
    multiplier = ONE
    if p.get("order_unit") != p.get("unit"):
        if not _number_valid(p.get("order_to_base_factor"), positive=True):
            add("unit_conversion")
            multiplier = None
        else:
            multiplier = decimal_number(p["order_to_base_factor"])
    for key in ("min_order_qty", "order_multiple"):
        if p.get(key) is not None and not _number_valid(p[key], positive=key == "order_multiple"):
            add("valid_order_constraints")
    if (_number_valid(p.get("order_multiple"), positive=True) and multiplier is not None
            and _number_valid(p.get("unit_quantum"), positive=True)):
        units = decimal_number(p["order_multiple"]) * multiplier / decimal_number(p["unit_quantum"])
        if units != units.to_integral_value():
            add("valid_order_constraints")
    if p.get("unit_cost") is not None and not _number_valid(p["unit_cost"]):
        add("valid_unit_cost")
    if request.get("budget_kzt") is not None and not _number_valid(request["budget_kzt"]):
        add("valid_budget_kzt")

    for field, date_key, unit_error in (("material_requirements", "needed_at", "material_unit_conversion"),
                                        ("inbound", "expected_at", "inbound_unit_conversion")):
        entries = p.get(field, [])
        if not isinstance(entries, list):
            add("valid_" + field)
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                add("valid_" + field)
                continue
            if entry.get("unit", p.get("unit")) != p.get("unit"):
                add(unit_error)
            if not _date_valid(entry.get(date_key)) or not _number_valid(entry.get("quantity")):
                add("valid_" + field)
            if field == "material_requirements":
                if not _number_valid(entry.get("already_accounted_quantity")):
                    add("valid_" + field)
                elif _number_valid(entry.get("quantity")) and decimal_number(entry["already_accounted_quantity"]) > decimal_number(entry["quantity"]):
                    add("valid_" + field)
            elif entry.get("status") not in {"confirmed", "cancelled", "planned", "unconfirmed"}:
                add("valid_" + field)

    distribution = p.get("horizon_distribution")
    valid = isinstance(distribution, list) and bool(distribution)
    if valid:
        valid = all(isinstance(v, dict) and _number_valid(v.get("quantity")) and _number_valid(v.get("probability"))
                    for v in distribution)
    if valid:
        mass = sum((decimal_number(v["probability"]) for v in distribution), ZERO)
        valid = mass > 0 and abs(mass - ONE) <= PROBABILITY_TOLERANCE
    if not valid:
        add("horizon_distribution")

    growths = p.get("growth_adjustments", [])
    included = p.get("growth_already_included", [])
    if not isinstance(included, list) or not all(isinstance(v, str) and v for v in included):
        add("valid_growth_adjustments")
    if not isinstance(growths, list):
        add("valid_growth_adjustments")
    else:
        by_id = {}
        for growth in growths:
            valid = (isinstance(growth, dict) and isinstance(growth.get("id"), str) and bool(growth["id"])
                     and _date_valid(growth.get("valid_from")) and _date_valid(growth.get("valid_to"))
                     and _number_valid(growth.get("rate"), nonnegative=False))
            if valid:
                valid = (decimal_number(growth["rate"], nonnegative=False) >= -1
                         and strict_date(growth["valid_from"]) <= strict_date(growth["valid_to"]))
            if not valid or (growth["id"] in by_id and growth != by_id[growth["id"]]):
                add("valid_growth_adjustments")
            elif valid:
                by_id[growth["id"]] = growth
    return missing


def _calculate_item(p, request, cutoff):
    result = {"sku": p["sku"], "supplier_id": p["supplier_id"], "quantity": None,
              "status": "ok", "rules": [], "missing": [], "reason": "", "unit": p.get("unit")}
    missing = _validate_item(p, request)
    if missing:
        result.update(status="needs_data", missing=missing, rules=["DATA-01"],
                      urgency="data_required",
                      reason="No quantity: required or invalid fields: " + ", ".join(missing))
        return result
    horizon = request["horizon_days"]
    end = cutoff + timedelta(days=horizon)
    policy = request["category_policies"].get(p["category_raw"])
    economics = p.get("economics")
    if economics is not None:
        underage, overage = (decimal_number(economics[key]) for key in ("underage_cost", "overage_cost"))
        q = underage / (underage + overage)
    else:
        q = decimal_number(policy["target_quantile"])
    floor = decimal_number(policy.get("minimum_target_quantile") or 0) if policy else ZERO
    q = max(q, floor)
    result["rules"].append("POLICY-01" if economics is not None else "POLICY-02")
    distribution = sorted(((decimal_number(v["quantity"]), decimal_number(v["probability"]))
                           for v in p["horizon_distribution"] if decimal_number(v["probability"]) > 0))
    mass = sum((prob for _, prob in distribution), ZERO)
    # Compare to q * accepted mass: normalization and CDF use identical precision,
    # including q=1. Zero-probability support must not determine q=0 or q=1.
    cumulative = ZERO
    target = distribution[-1][0]
    for quantity, probability in distribution:
        cumulative += probability
        if cumulative >= q * mass:
            target = quantity
            break
    daily = [decimal_number(v) for v in p["daily_mean"]]
    original_mean = sum(daily, ZERO)
    applied = set(p.get("growth_already_included", []))
    for growth in p.get("growth_adjustments", []):
        if growth["id"] in applied:
            continue
        start, stop = strict_date(growth["valid_from"]), strict_date(growth["valid_to"])
        for day in range(horizon):
            if start <= cutoff + timedelta(days=day + 1) <= stop:
                daily[day] *= ONE + decimal_number(growth["rate"], nonnegative=False)
        applied.add(growth["id"])
        result["rules"].append("DEMAND-04")
    forecast_mean = sum(daily, ZERO)
    if original_mean:
        target *= forecast_mean / original_mean
    free = decimal_number(p["free_stock"]) if p.get("free_stock") is not None else decimal_number(p["on_hand"]) - decimal_number(p["reserved"])
    commitments = [(strict_date(m["needed_at"]), decimal_number(m["quantity"]) - decimal_number(m["already_accounted_quantity"]))
                   for m in p.get("material_requirements", []) if strict_date(m["needed_at"]) <= end]
    inbound = [(strict_date(i["expected_at"]), decimal_number(i["quantity"]))
               for i in p.get("inbound", []) if i["status"] == "confirmed" and cutoff < strict_date(i["expected_at"]) <= end]
    uncovered = sum((quantity for _, quantity in commitments), ZERO)
    inbound_qty = sum((quantity for _, quantity in inbound), ZERO)
    need = max(ZERO, target + uncovered - free - inbound_qty)
    quantum = decimal_number(p["unit_quantum"])
    multiplier = ONE if p["order_unit"] == p["unit"] else decimal_number(p["order_to_base_factor"])
    moq = None if p.get("min_order_qty") is None else decimal_number(p["min_order_qty"]) * multiplier
    multiple = None if p.get("order_multiple") is None else decimal_number(p["order_multiple"]) * multiplier
    provisional = moq is None or multiple is None
    if provisional:
        quantity = _round_decimal(need, quantum)
        result.update(status="needs_review", quantity_kind="provisional_base_need")
        result["rules"].append("SUPPLY-04")
    else:
        quantity = _round_decimal(max(need, moq), multiple) if need > 0 else ZERO
        result["quantity_kind"] = "constrained_recommendation"
        result["rules"].append("SUPPLY-03")

    balance = free
    first_deficit, early_shortfall = None, ZERO
    # The original no-order diagnostic is retained for existing integrations.
    with_order_balance = free
    physical = max(ZERO, free)
    residual_first, residual_shortfall, residual_unmet = None, ZERO, ZERO
    receipt_day = max(1, p["lead_time_days"])
    for day, demand in enumerate(daily, 1):
        stamp = cutoff + timedelta(days=day)
        receipt = sum((v for eta, v in inbound if eta == stamp), ZERO)
        obligations = sum((v for needed, v in commitments if needed == stamp or (day == 1 and needed < stamp)), ZERO)
        outflow = demand + obligations
        balance += receipt - outflow
        if balance < 0:
            first_deficit = first_deficit or day
            if day < receipt_day:
                early_shortfall = max(early_shortfall, -balance)
        normal = quantity if day == receipt_day else ZERO
        with_order_balance += receipt + normal - outflow
        unmet = max(ZERO, outflow - physical - receipt - normal)
        physical = max(ZERO, physical + receipt + normal - outflow)
        if day >= receipt_day:
            if with_order_balance < 0:
                residual_first = residual_first or day
                residual_shortfall = max(residual_shortfall, -with_order_balance)
            residual_unmet += unmet
    if early_shortfall or residual_shortfall:
        result["rules"].append("SUPPLY-02")
    if p.get("reserved", 0) or commitments:
        result["rules"].append("SUPPLY-01")
    urgency = ("expedite" if early_shortfall or residual_shortfall else
               "review_required" if provisional else "no_order" if quantity == 0 else "routine")
    result.update(quantity=quantity, forecast_mean=forecast_mean, target_stock=target,
                  urgency=urgency,
                  target_quantile=q, free_stock=free, material_uncovered=uncovered,
                  inbound_in_horizon=inbound_qty, first_deficit_day=first_deficit,
                  first_deficit_day_basis="without_proposed_order",
                  early_shortfall=early_shortfall,
                  residual_deficit_day=residual_first, residual_shortfall=residual_shortfall,
                  residual_unmet_quantity=residual_unmet,
                  requires_expedite=bool(early_shortfall or residual_shortfall),
                  economics_source=economics["source"] if economics is not None else None,
                  unit_cost=decimal_number(p["unit_cost"]) if p.get("unit_cost") is not None else None,
                  order_constraints_base={"min_order_qty": moq, "order_multiple": multiple, "unit_quantum": quantum})
    lot = (f"provisional need rounded to quantum {_display(quantum)}; MOQ={_display(moq)}, multiple={_display(multiple)}; supplier review required"
           if provisional else f"MOQ={_display(moq)}, multiple={_display(multiple)}")
    result["reason"] = (f"Forecast {_display(forecast_mean)} {p['unit']} over {horizon} days; quantile {_display(q)} gives target {_display(target)}. "
                        f"Uncovered commitments {_display(uncovered)}, free stock {_display(free)}, confirmed inbound {_display(inbound_qty)}: "
                        f"base need max(0, {_display(target)}+{_display(uncovered)}-{_display(free)}-{_display(inbound_qty)})={_display(need)}; {lot}; quantity {_display(quantity)}. "
                        f"Before normal ETA day {receipt_day}: shortfall {_display(early_shortfall)}. "
                        f"With proposed order: residual shortage {_display(residual_shortfall)}, first residual day {residual_first}, "
                        f"post-ETA unmet demand {_display(residual_unmet)}. Manual approval is required. "
                        "Displayed numbers use up to six significant digits.")
    return result


def reference_calculate(request):
    """Illustrative local contract, with explicit validation and manual approval."""
    if not isinstance(request, dict) or not _integer(request.get("horizon_days"), 1):
        raise ValueError("horizon_days must be a positive integer")
    cutoff = strict_date(request.get("as_of"))
    if not isinstance(request.get("category_policies"), dict) or not isinstance(request.get("items"), list):
        raise ValueError("category_policies must be an object and items a list")
    identities = set()
    for p in request["items"]:
        if not isinstance(p, dict) or any(not isinstance(p.get(k), str) or not p[k].strip() for k in ("sku", "supplier_id", "category_raw")):
            raise ValueError("item identifiers must be nonempty strings")
        if p["sku"] in identities:
            raise ValueError("duplicate SKU")
        identities.add(p["sku"])
    with localcontext() as context:
        context.prec = 80
        items = [_calculate_item(p, request, cutoff) for p in request["items"]]
        known_total = sum((i["quantity"] * i["unit_cost"] for i in items if i["quantity"] is not None and i.get("unit_cost") is not None), ZERO)
        unknown_price = any(i["quantity"] is not None and i["quantity"] > 0 and i.get("unit_cost") is None for i in items)
        budget = request.get("budget_kzt")
        budget = decimal_number(budget) if budget is not None and _number_valid(budget) else None
        rules = []
        if budget is not None and unknown_price:
            rules.append("BUDGET-01")
        if budget is not None and known_total > budget:
            rules.append("BUDGET-02")
        incomplete = any(i["quantity"] is None for i in items)
        response = {"items": items, "known_cost_kzt": known_total,
                    "total_cost_kzt": None if unknown_price or incomplete else known_total,
                    "budget_excess_kzt": max(ZERO, known_total - budget) if budget is not None else None,
                    "approval_blocked": bool(rules) or any(i["status"] != "ok" for i in items),
                    "requires_manual_approval": True, "rules": rules,
                    "supplier_groups": {supplier: [i["sku"] for i in items if i["supplier_id"] == supplier]
                                        for supplier in sorted({i["supplier_id"] for i in items})}}
        return _json_numbers(response)


def validate_response(request, actual):
    with localcontext() as context:
        context.prec = 80
        try:
            return _validate_response(request, actual)
        except (TypeError, ValueError, ArithmeticError, KeyError) as exc:
            return [f"response: malformed value ({type(exc).__name__}: {exc})"]


def _validate_response(request, actual):
    """Shared schema/invariants applied before any fixture's subset assertions.

This does not recompute forecast or recommendation quantities as its own oracle.
It checks types, identities, physical constraints, accounting and status coherence.
"""
    failures = []
    def fail(message):
        failures.append("response: " + message)
    def number(value, field, nullable=False, signed=False):
        if value is None and nullable:
            return True
        if not _number_valid(value, nonnegative=not signed):
            fail(field + " must be a finite " + ("number" if signed else "nonnegative number"))
            return False
        return True
    def rule_list(value, field, nonempty=False):
        if not isinstance(value, list) or (nonempty and not value) or not all(isinstance(v, str) and v.strip() for v in value):
            fail(field + " must be a list of rule IDs")
        elif any(v not in RULE_IDS for v in value):
            fail(field + " contains unknown rule IDs")

    if not isinstance(actual, dict):
        return ["response: expected object"]
    required_top = {"items", "rules", "approval_blocked", "known_cost_kzt", "total_cost_kzt", "budget_excess_kzt", "supplier_groups"}
    for key in sorted(required_top - actual.keys()):
        fail("missing field " + key)
    if not isinstance(actual.get("items"), list) or len(actual["items"]) != len(request["items"]):
        return ["response: exact item count required"]
    rule_list(actual.get("rules"), "rules")
    if type(actual.get("approval_blocked")) is not bool:
        fail("approval_blocked must be boolean")
    # New metadata is optional for external adapters; if present it cannot claim
    # authority to automatically execute an order.
    if "requires_manual_approval" in actual and actual["requires_manual_approval"] is not True:
        fail("orders require manual approval")
    requests = {p["sku"]: p for p in request["items"]}
    seen, grouped = set(), {}
    cost = ZERO
    incomplete = False
    unknown_price = False
    non_ok = False
    for index, item in enumerate(actual["items"]):
        prefix = f"items[{index}]"
        if not isinstance(item, dict):
            fail(prefix + " must be an object")
            continue
        for key in sorted({"sku", "supplier_id", "quantity", "status", "rules", "missing", "reason", "urgency"} - item.keys()):
            fail(prefix + " missing field " + key)
        sku = item.get("sku")
        if not isinstance(sku, str) or sku not in requests or sku in seen:
            fail(prefix + " must have a unique requested SKU")
            continue
        seen.add(sku)
        source = requests[sku]
        if item.get("supplier_id") != source["supplier_id"]:
            fail(prefix + " supplier differs from request")
        grouped.setdefault(source["supplier_id"], []).append(sku)
        if item.get("unit") is not None and item["unit"] != source["unit"]:
            fail(prefix + " unit differs from base unit")
        if not isinstance(item.get("reason"), str) or not item["reason"].strip():
            fail(prefix + " reason must be nonempty text")
        rule_list(item.get("rules"), prefix + ".rules", True)
        if not isinstance(item.get("missing"), list) or not all(isinstance(v, str) and v for v in item["missing"]):
            fail(prefix + " missing must be a list of field names")
        status = item.get("status")
        if not isinstance(status, str) or status not in {"ok", "needs_data", "needs_review"}:
            fail(prefix + " invalid status")
        non_ok |= status != "ok"
        quantity = item.get("quantity")
        if status == "needs_data":
            incomplete = True
            if item.get("urgency") != "data_required":
                fail(prefix + " needs_data requires data_required urgency")
            if quantity is not None or not item.get("missing"):
                fail(prefix + " needs_data requires null quantity and missing fields")
            if not isinstance(item.get("rules"), list) or "DATA-01" not in item["rules"]:
                fail(prefix + " needs_data requires DATA-01")
            continue
        if not number(quantity, prefix + ".quantity"):
            continue
        required_calculated = {"forecast_mean", "target_stock", "target_quantile", "free_stock", "material_uncovered",
                               "inbound_in_horizon", "first_deficit_day", "early_shortfall", "requires_expedite",
                               "economics_source", "unit", "unit_cost"}
        for key in sorted(required_calculated - item.keys()):
            fail(prefix + " missing field " + key)
        if item.get("unit") != source["unit"]:
            fail(prefix + " quantity needs the requested base unit")
        if item.get("missing"):
            fail(prefix + " calculated quantity conflicts with missing fields")
        q = decimal_number(quantity)
        for key in ("forecast_mean", "target_stock", "target_quantile", "material_uncovered", "inbound_in_horizon", "early_shortfall"):
            number(item.get(key), prefix + "." + key)
        number(item.get("free_stock"), prefix + ".free_stock", signed=True)
        if _number_valid(item.get("target_quantile")) and decimal_number(item["target_quantile"]) > 1:
            fail(prefix + " target_quantile exceeds1")
        for key in ("first_deficit_day", "residual_deficit_day"):
            if key in item and item[key] is not None and (not _integer(item[key], 1) or item[key] > request["horizon_days"]):
                fail(prefix + "." + key + " outside horizon")
        for key in ("residual_shortfall", "residual_unmet_quantity"):
            if key in item:
                number(item[key], prefix + "." + key)
        if type(item.get("requires_expedite")) is not bool:
            fail(prefix + " requires_expedite must be boolean")
        has_shortage = any(_number_valid(item.get(key)) and decimal_number(item[key]) > 0 for key in ("early_shortfall", "residual_shortfall"))
        if has_shortage and (item.get("requires_expedite") is not True or not isinstance(item.get("rules"), list) or "SUPPLY-02" not in item["rules"]):
            fail(prefix + " shortage requires explicit SUPPLY-02 and urgency")
        expected_urgency = ("expedite" if has_shortage else "review_required" if status == "needs_review"
                            else "no_order" if q == 0 else "routine")
        if item.get("urgency") != expected_urgency:
            fail(prefix + " urgency disagrees with shortage, review status or quantity")
        expected_source = source["economics"].get("source") if isinstance(source.get("economics"), dict) else None
        if item.get("economics_source") != expected_source:
            fail(prefix + " economic provenance differs from input")
        quantum = source.get("unit_quantum")
        if _number_valid(quantum, positive=True) and q % decimal_number(quantum) != 0:
            fail(prefix + " quantity violates physical quantum")
        known = source.get("min_order_qty") is not None and source.get("order_multiple") is not None
        if not known:
            if status != "needs_review":
                fail(prefix + " missing supplier constraints requires needs_review")
            if isinstance(item.get("rules"), list) and "SUPPLY-04" not in item["rules"]:
                fail(prefix + " provisional need requires SUPPLY-04")
            if "quantity_kind" in item and item["quantity_kind"] != "provisional_base_need":
                fail(prefix + " unknown constraints cannot claim a valid lot")
        elif q > 0:
            factor = 1 if source["unit"] == source["order_unit"] else source.get("order_to_base_factor")
            if all(_number_valid(v, positive=True) for v in (factor, source["order_multiple"])) and _number_valid(source["min_order_qty"]):
                minimum = decimal_number(source["min_order_qty"]) * decimal_number(factor)
                multiple = decimal_number(source["order_multiple"]) * decimal_number(factor)
                if q < minimum or q % multiple != 0:
                    fail(prefix + " quantity violates MOQ/multiple")
        price = item.get("unit_cost")
        if not number(price, prefix + ".unit_cost", nullable=True):
            continue
        if price != source.get("unit_cost"):
            fail(prefix + " price differs from source")
        if price is None:
            unknown_price |= q > 0
        else:
            cost += q * decimal_number(price)
    if seen != set(requests):
        fail("response must cover every requested SKU")
    expected_groups = {key: sorted(value) for key, value in grouped.items()}
    groups = actual.get("supplier_groups")
    if not isinstance(groups, dict) or not all(isinstance(v, list) and all(isinstance(s, str) for s in v) for v in groups.values()) or {key: sorted(value) for key, value in groups.items()} != expected_groups:
        fail("supplier groups disagree with item/request identities")
    for key in ("known_cost_kzt", "total_cost_kzt", "budget_excess_kzt"):
        number(actual.get(key), key, nullable=key != "known_cost_kzt")
    if compare(cost, actual.get("known_cost_kzt")):
        fail("known_cost_kzt does not equal sum(quantity * price)")
    expected_total = None if incomplete or unknown_price else cost
    if compare(expected_total, actual.get("total_cost_kzt")):
        fail("total_cost_kzt inconsistent with missing quantities/prices")
    budget = request.get("budget_kzt")
    valid_budget = budget is not None and _number_valid(budget)
    expected_excess = max(ZERO, cost - decimal_number(budget)) if valid_budget else None
    if compare(expected_excess, actual.get("budget_excess_kzt")):
        fail("budget excess inconsistent with known cost")
    budget_rules = []
    if valid_budget and unknown_price:
        budget_rules.append("BUDGET-01")
    if valid_budget and cost > decimal_number(budget):
        budget_rules.append("BUDGET-02")
    if isinstance(actual.get("rules"), list):
        if any(rule not in actual["rules"] for rule in budget_rules) or any(rule.startswith("BUDGET-") and rule not in budget_rules for rule in actual["rules"]):
            fail("budget rules inconsistent with costs/prices")
    if (non_ok or budget_rules) and actual.get("approval_blocked") is not True:
        fail("invalid, provisional or over-budget order cannot be approved")
    return failures


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
    elif isinstance(expected, (int, float, Decimal)) and not isinstance(expected, bool):
        valid = _number_valid(actual, nonnegative=False) and _number_valid(expected, nonnegative=False)
        if valid:
            wanted = decimal_number(expected, nonnegative=False)
            received = decimal_number(actual, nonnegative=False)
            tolerance = max(Decimal("1e-8"), Decimal("1e-9") * max(abs(wanted), abs(received)))
            valid = abs(wanted - received) <= tolerance
        if not valid:
            failures.append(f"{path}: expected {expected!r}, got {actual!r}")
    elif type(expected) is not type(actual) or expected != actual:
        failures.append(f"{path}: expected {expected!r}, got {actual!r}")
    return failures


def check_cases(calculator):
    fixture = json.loads(Path(__file__).with_name("fixtures").joinpath("business_cases.json").read_text())
    rows = []
    for case in fixture["cases"]:
        try:
            actual = calculator(deepcopy(case["input"]))
            failures = validate_response(case["input"], actual) + compare(case["expected"], actual)
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
