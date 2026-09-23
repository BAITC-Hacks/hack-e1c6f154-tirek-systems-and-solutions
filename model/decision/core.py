"""Offline, dated, scenario-based replenishment; no network or order submission.

Dates are calendar dates. ``as_of`` is the end of the observed day; scenario
column 0 is the next day. Receipts arrive before that day's demand. Missing
required information is returned explicitly; malformed information raises
ValueError. See README.md for the lost-sales and material-accounting contract.
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from math import isfinite
from typing import Mapping, Sequence


@dataclass(frozen=True)
class InventorySnapshot:
    as_of: date
    on_hand: float | None = None
    reserved: float | None = None
    free_stock: float | None = None
    source: str = ""


@dataclass(frozen=True)
class Inbound:
    event_id: str
    quantity: float
    expected_at: date
    unit: str
    source: str


@dataclass(frozen=True)
class MaterialRequirement:
    requirement_id: str
    quantity: float
    due_at: date
    unit: str
    source: str
    included_in_forecast: float = 0.0
    already_reserved: float = 0.0
    accounting_source: str = ""
    hard: bool = True


@dataclass(frozen=True)
class GrowthAdjustment:
    adjustment_id: str
    rate: float
    starts_at: date
    ends_at: date
    source: str


@dataclass(frozen=True)
class SupplierConstraint:
    order_unit: str
    min_order_qty: float | None = None
    order_multiple: float | None = None
    inventory_units_per_order_unit: float | None = None
    source: str = ""


@dataclass(frozen=True)
class EconomicProfile:
    underage_cost: float
    overage_cost: float
    horizon_days: int
    currency: str
    source: str


@dataclass(frozen=True)
class ServicePolicy:
    service_level: float
    source: str
    version: str


@dataclass(frozen=True)
class RecommendationInput:
    sku: str
    warehouse_id: str
    supplier_id: str
    unit: str
    as_of: date
    forecast_as_of: date
    scenarios: Sequence[Sequence[float]] | None
    forecast_id: str
    unit_quantum: float | None = None  # Physical inventory quantum; explicit, not inferred from its label.
    inventory: InventorySnapshot | None = None
    lead_time_days: int | None = None
    review_period_days: int | None = None
    constraints: SupplierConstraint | None = None
    economics: EconomicProfile | None = None
    service_policy: ServicePolicy | None = None
    category_id: str | None = None
    category_policies: Mapping[str, ServicePolicy] = field(default_factory=dict)
    inbound: Sequence[Inbound] = ()
    materials: Sequence[MaterialRequirement] = ()
    growth_adjustments: Sequence[GrowthAdjustment] = ()
    included_growth_ids: Sequence[str] = ()
    unit_cost: float | None = None  # Per inventory unit, for approval budget only.
    price_currency: str | None = None
    max_snapshot_age_days: int = 0
    max_forecast_age_days: int = 0
    data_version: str = "unspecified"
    rule_version: str = "decision-core-1"


@dataclass(frozen=True)
class Recommendation:
    sku: str
    warehouse_id: str
    supplier_id: str
    unit: str
    as_of: date
    status: str
    quantity: float | None
    provisional_quantity: float | None
    reason: str
    urgency: str
    warnings: tuple[str, ...]
    required_fields: tuple[str, ...]
    unit_cost: float | None
    price_currency: str | None
    diagnostics: dict


@dataclass(frozen=True)
class ApprovalCheck:
    allowed: bool
    reasons: tuple[str, ...]
    total_cost: float | None
    currency: str | None
    revision: int


def _number(value: float, name: str, *, minimum: float = 0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name}: expected a finite number")
    if not isfinite(value) or value < minimum:
        raise ValueError(f"{name}: must be finite and >= {minimum}")
    return float(value)


def _date(value: date, name: str) -> None:
    if type(value) is not date:
        raise ValueError(f"{name}: expected datetime.date (not datetime)")


def _integer(value: int, name: str, minimum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name}: expected integer >= {minimum}")


def _text(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name}: must be a nonempty string")


def _unique(values: Sequence[str], name: str) -> None:
    for value in values:
        _text(value, name)
    if len(set(values)) != len(values):
        raise ValueError(f"{name}: duplicate id")


def _policy(policy: ServicePolicy) -> None:
    if _number(policy.service_level, "service_level") > 1:
        raise ValueError("service_level: must be <= 1")
    _text(policy.source, "policy.source")
    _text(policy.version, "policy.version")


def _quantile(values: Sequence[float], probability: float) -> float:
    # Smallest stock covering at least probability of equal-weight scenarios.
    if probability <= 0:
        return 0.0
    rank = int((Decimal(str(probability)) * len(values)).to_integral_value(rounding=ROUND_CEILING))
    return sorted(values)[max(0, rank - 1)]


def _batch(quantity: float, multiple: float | None) -> float:
    if multiple is None:
        return quantity
    step = Decimal(str(multiple))
    return float((Decimal(str(quantity)) / step).to_integral_value(rounding=ROUND_CEILING) * step)


def _on_lattice(quantity: float, quantum: float) -> bool:
    ratio = Decimal(str(quantity)) / Decimal(str(quantum))
    return ratio == ratio.to_integral_value()


def _physical(value: float, name: str, quantum: float | None) -> None:
    _number(value, name)
    if quantum is not None and not _on_lattice(value, quantum):
        raise ValueError(name + ": must be a multiple of physical unit_quantum")


def _quantity_sum(left: float, right: float, name: str) -> float:
    result = float(Decimal(str(left)) + Decimal(str(right)))
    return _number(result, name)


def _uncovered_material(item: MaterialRequirement) -> float:
    return float(Decimal(str(item.quantity)) - Decimal(str(item.included_in_forecast))
                 - Decimal(str(item.already_reserved)))


def _choose_quantity(
    requirements: Sequence[float], floor_qty: float, minimum: float | None,
    multiple: float | None, economics: EconomicProfile | None,
) -> float:
    """Exact piecewise-linear convex minimization over 0 or MOQ/multiple lots."""
    positive_floor = max(floor_qty, minimum or 0.0)
    lower = _batch(positive_floor, multiple)
    candidates = {lower}
    if floor_qty <= 0:
        candidates.add(0.0)
    for point in requirements:
        if multiple is None:
            candidates.add(max(point, positive_floor))
        else:
            step = Decimal(str(multiple))
            ratio = Decimal(str(point)) / step
            candidates.add(max(float(ratio.to_integral_value(rounding=ROUND_FLOOR) * step), lower))
            candidates.add(max(float(ratio.to_integral_value(rounding=ROUND_CEILING) * step), lower))
    feasible = [q for q in candidates if q >= floor_qty and (q == 0 or q >= (minimum or 0))]
    if economics is None:
        return min(feasible)
    # Pre-arrival loss and baseline ending stock are constant in Q, so they
    # cancel during minimization. They are included in reported total loss.
    demand = sorted(Decimal(str(r)) for r in requirements)
    prefix = [Decimal(0)]
    for amount in demand:
        prefix.append(prefix[-1] + amount)
    underage = Decimal(str(economics.underage_cost))
    overage = Decimal(str(economics.overage_cost))
    def objective(q: float) -> Decimal:
        amount = Decimal(str(q))
        # Division by scenario count is a positive common factor, unnecessary
        # for comparison. Decimal preserves exact ties (prefer less capital).
        count = bisect_right(demand, amount)
        shortage = prefix[-1] - prefix[count] - amount * (len(demand) - count)
        excess = amount * count - prefix[count]
        return underage * shortage + overage * excess
    return min(feasible, key=lambda q: (objective(q), q))


def _simulate_without_order(
    scenarios: Sequence[Sequence[float]], free_stock: float, lead: int,
    arrivals: Sequence[float], hard_material: Sequence[float],
    soft_material: Sequence[float],
) -> tuple[list[float], list[float], list[float], float, list[float]]:
    early_loss, later_loss, ending_stock, early_material = [], [], [], []
    material_floor = Decimal(0)
    for scenario in scenarios:
        stock = Decimal(str(free_stock))
        early = later = material_early = Decimal(0)
        for index, regular in enumerate(scenario):
            day = index + 1
            stock += Decimal(str(arrivals[index]))
            # Uncovered confirmed hard commitments get first claim that day;
            # reserved units were already removed from free_stock separately.
            hard_lost = max(Decimal(str(hard_material[index])) - stock, Decimal(0))
            stock = max(stock - Decimal(str(hard_material[index])), Decimal(0))
            if day < lead:
                early += hard_lost
                material_early += hard_lost
            else:
                later += hard_lost
                if hard_lost > 0:
                    material_floor = max(material_floor, later)
            other_demand = Decimal(str(regular)) + Decimal(str(soft_material[index]))
            other_lost = max(other_demand - stock, Decimal(0))
            stock = max(stock - other_demand, Decimal(0))
            if day < lead:
                early += other_lost
            else:
                later += other_lost
        early_loss.append(_number(float(early), "simulated early loss"))
        later_loss.append(_number(float(later), "simulated later loss"))
        ending_stock.append(_number(float(stock), "simulated ending stock"))
        early_material.append(_number(float(material_early), "simulated material loss"))
    return early_loss, later_loss, ending_stock, _number(float(material_floor), "material floor"), early_material


def recommend(request: RecommendationInput) -> Recommendation:
    """Recommend a lot from joint daily scenarios; never send or approve it.

    Each scenario has equal probability and must represent regular latent
    demand, with its own uncertainty. All quantities are in request.unit.
    """
    r = request
    for name in ("sku", "warehouse_id", "supplier_id", "unit", "forecast_id", "data_version", "rule_version"):
        _text(getattr(r, name), name)
    _date(r.as_of, "as_of")
    _date(r.forecast_as_of, "forecast_as_of")
    _integer(r.max_snapshot_age_days, "max_snapshot_age_days", 0)
    _integer(r.max_forecast_age_days, "max_forecast_age_days", 0)
    if r.forecast_as_of > r.as_of:
        raise ValueError("forecast_as_of cannot be in the future")
    warnings: list[str] = []
    missing: list[str] = []
    if r.unit_quantum is None:
        missing.append("unit_quantum")
    elif _number(r.unit_quantum, "unit_quantum") == 0:
        raise ValueError("unit_quantum must be positive")
    if (r.as_of - r.forecast_as_of).days > r.max_forecast_age_days:
        missing.append("fresh_forecast")
    for name, minimum in (("lead_time_days", 0), ("review_period_days", 1)):
        value = getattr(r, name)
        if value is None:
            missing.append(name)
        else:
            _integer(value, name, minimum)
    if r.unit_cost is not None:
        _number(r.unit_cost, "unit_cost")
        _text(r.price_currency, "price_currency")
    elif r.price_currency is not None:
        _text(r.price_currency, "price_currency")
    free_stock = None
    if r.inventory is None:
        missing.append("inventory")
    else:
        snapshot = r.inventory
        _date(snapshot.as_of, "inventory.as_of")
        if snapshot.as_of > r.as_of:
            raise ValueError("inventory.as_of cannot be in the future")
        if (r.as_of - snapshot.as_of).days > r.max_snapshot_age_days:
            missing.append("fresh_inventory")
        for name in ("on_hand", "reserved", "free_stock"):
            value = getattr(snapshot, name)
            if value is not None:
                _physical(value, "inventory." + name, r.unit_quantum)
        if snapshot.on_hand is not None and snapshot.reserved is not None:
            if snapshot.reserved > snapshot.on_hand:
                raise ValueError("inventory.reserved exceeds on_hand")
            free_stock = float(Decimal(str(snapshot.on_hand)) - Decimal(str(snapshot.reserved)))
            if snapshot.free_stock is not None and abs(free_stock - snapshot.free_stock) > 1e-8:
                raise ValueError("inventory.free_stock disagrees with on_hand - reserved")
        if snapshot.free_stock is not None:
            free_stock = snapshot.free_stock  # Never subtract reserved again.
        if free_stock is None:
            missing.append("inventory.free_stock_or_on_hand_and_reserved")
    policies = []
    if r.service_policy is not None:
        _policy(r.service_policy)
        policies.append(r.service_policy)
    for key, value in r.category_policies.items():
        _text(key, "category policy key")
        _policy(value)
    if r.category_id is not None:
        _text(r.category_id, "category_id")
        if r.category_id in r.category_policies:
            policies.append(r.category_policies[r.category_id])
        else:
            warnings.append("category_policy_unmapped")
    if r.economics is not None:
        economics = r.economics
        _number(economics.underage_cost, "underage_cost")
        _number(economics.overage_cost, "overage_cost")
        if economics.underage_cost + economics.overage_cost <= 0:
            raise ValueError("at least one economic loss must be positive")
        _integer(economics.horizon_days, "economics.horizon_days", 1)
        _text(economics.currency, "economics.currency")
        _text(economics.source, "economics.source")
    elif not policies:
        missing.append("economics_or_service_policy")
    scenarios = []
    if r.scenarios is None:
        missing.append("scenarios")
    else:
        if len(r.scenarios) == 0:
            raise ValueError("scenarios must not be empty")
        for scenario in r.scenarios:
            if len(scenario) == 0:
                raise ValueError("scenario horizon must not be empty")
            scenarios.append([_number(x, "scenario demand") for x in scenario])
        if any(len(s) != len(scenarios[0]) for s in scenarios):
            raise ValueError("scenarios must share one horizon")
    horizon = len(scenarios[0]) if scenarios else None
    if horizon is not None:
        if r.lead_time_days is not None and r.review_period_days is not None:
            if r.lead_time_days + r.review_period_days != horizon:
                raise ValueError("horizon must equal lead_time_days + review_period_days")
        if r.economics is not None and r.economics.horizon_days != horizon:
            raise ValueError("economics and demand must use the same horizon")
    _unique([x.event_id for x in r.inbound], "inbound.event_id")
    _unique([x.requirement_id for x in r.materials], "materials.requirement_id")
    _unique([x.adjustment_id for x in r.growth_adjustments], "growth.adjustment_id")
    _unique(r.included_growth_ids, "included_growth_ids")
    for item in r.inbound:
        _physical(item.quantity, "inbound.quantity", r.unit_quantum)
        _date(item.expected_at, "inbound.expected_at")
        _text(item.source, "inbound.source")
        if item.unit != r.unit:
            raise ValueError("inbound unit must be converted to inventory unit explicitly")
        if item.expected_at <= r.as_of:
            missing.append("refreshed_eta:" + item.event_id)
    reserved_material = 0.0
    for item in r.materials:
        for name in ("quantity", "included_in_forecast", "already_reserved"):
            _physical(getattr(item, name), "material." + name, r.unit_quantum)
        _date(item.due_at, "material.due_at")
        _text(item.source, "material.source")
        if not isinstance(item.hard, bool):
            raise ValueError("material.hard must be boolean")
        if item.unit != r.unit:
            raise ValueError("material unit must be converted to inventory unit explicitly")
        if _uncovered_material(item) < 0:
            raise ValueError("material accounting exceeds quantity; allocations must be disjoint")
        if item.included_in_forecast or item.already_reserved:
            _text(item.accounting_source, "material.accounting_source")
        reserved_material = _quantity_sum(reserved_material, item.already_reserved, "total material reserve")
        if item.due_at <= r.as_of and item.quantity > item.included_in_forecast + item.already_reserved:
            missing.append("refreshed_material_due_at:" + item.requirement_id)
    if r.inventory is not None and r.inventory.reserved is not None:
        if reserved_material > r.inventory.reserved:
            raise ValueError("material reserved allocations exceed inventory reserved")
    for adjustment in r.growth_adjustments:
        _number(adjustment.rate, "growth.rate", minimum=-1)
        _date(adjustment.starts_at, "growth.starts_at")
        _date(adjustment.ends_at, "growth.ends_at")
        _text(adjustment.source, "growth.source")
        if adjustment.ends_at < adjustment.starts_at:
            raise ValueError("growth end is before start")
    minimum = multiple = None
    constraint_unknown: list[str] = []
    if r.constraints is None:
        constraint_unknown.extend(["min_order_qty", "order_multiple"])
    else:
        constraints = r.constraints
        _text(constraints.order_unit, "constraints.order_unit")
        for name in ("min_order_qty", "order_multiple", "inventory_units_per_order_unit"):
            value = getattr(constraints, name)
            if value is not None:
                _number(value, "constraints." + name)
                if name != "min_order_qty" and value == 0:
                    raise ValueError("constraints." + name + " must be positive")
        conversion = constraints.inventory_units_per_order_unit
        if constraints.order_unit == r.unit:
            if conversion is not None and conversion != 1:
                raise ValueError("same-unit conversion must equal 1")
            conversion = 1.0  # Dimensional identity, not an unknown conversion.
        elif conversion is None:
            constraint_unknown.append("inventory_units_per_order_unit")
        for name in ("min_order_qty", "order_multiple"):
            if getattr(constraints, name) is None:
                constraint_unknown.append(name)
        if conversion is not None:
            factor = Decimal(str(conversion))
            minimum = None if constraints.min_order_qty is None else float(Decimal(str(constraints.min_order_qty)) * factor)
            multiple = None if constraints.order_multiple is None else float(Decimal(str(constraints.order_multiple)) * factor)
            if minimum is not None:
                _number(minimum, "converted min_order_qty")
            if multiple is not None:
                _number(multiple, "converted order_multiple")
                if r.unit_quantum is not None and not _on_lattice(multiple, r.unit_quantum):
                    raise ValueError("converted order_multiple must be a multiple of unit_quantum")
    warnings.extend("unknown_constraint:" + x for x in constraint_unknown)
    diagnostics = {
        "forecast_id": r.forecast_id, "data_version": r.data_version,
        "forecast_as_of": r.forecast_as_of.isoformat(),
        "inventory_as_of": r.inventory.as_of.isoformat() if r.inventory else None,
        "rule_version": r.rule_version, "horizon_days": horizon,
        "category_id": r.category_id, "free_stock": free_stock,
        "unit_quantum": r.unit_quantum,
        "inventory_source": r.inventory.source if r.inventory is not None else None,
        "policy_sources": [p.source for p in policies],
        "policy_versions": [p.version for p in policies],
        "economics_source": r.economics.source if r.economics else None,
        "constraint_source": r.constraints.source if r.constraints else None,
    }
    def result(status: str, quantity: float | None, provisional: float | None,
               reason: str, urgency: str) -> Recommendation:
        return Recommendation(r.sku, r.warehouse_id, r.supplier_id, r.unit, r.as_of,
                              status, quantity, provisional, reason, urgency,
                              tuple(dict.fromkeys(warnings)), tuple(dict.fromkeys(missing)),
                              r.unit_cost, r.price_currency, diagnostics)
    if missing:
        return result("needs_data", None, None, "Расчёт требует данных: " + ", ".join(missing), "unknown")
    assert horizon is not None and free_stock is not None and r.lead_time_days is not None and r.unit_quantum is not None
    days = [r.as_of + timedelta(days=i + 1) for i in range(horizon)]
    diagnostics["scenario_demand_mean_before_material_split"] = sum(sum(x) for x in scenarios) / len(scenarios)
    # Reclassify declared forecast allocations as dated commitments, so hard
    # service applies to them too and growth never scales a fixed obligation.
    # The total demand increases only by the previously uncovered component.
    forecast_allocations = [0.0] * horizon
    for item in r.materials:
        index = (item.due_at - r.as_of).days - 1
        if 0 <= index < horizon:
            forecast_allocations[index] = _quantity_sum(forecast_allocations[index], item.included_in_forecast, "material forecast allocations")
    for scenario in scenarios:
        for index, allocated in enumerate(forecast_allocations):
            remaining = Decimal(str(scenario[index])) - Decimal(str(allocated))
            if remaining < 0:
                raise ValueError("material included_in_forecast exceeds a scenario on its due date")
            scenario[index] = float(remaining)
    growth_log = []
    for adjustment in r.growth_adjustments:
        applicable = [i for i, day in enumerate(days) if adjustment.starts_at <= day <= adjustment.ends_at]
        already_included = adjustment.adjustment_id in r.included_growth_ids
        if not already_included:
            for scenario in scenarios:
                for i in applicable:
                    scenario[i] = float(Decimal(str(scenario[i])) * (Decimal(1) + Decimal(str(adjustment.rate))))
                    if not isfinite(scenario[i]):
                        raise ValueError("growth-adjusted demand overflow")
        growth_log.append({"id": adjustment.adjustment_id, "source": adjustment.source,
                           "rate": adjustment.rate, "affected_days": len(applicable),
                           "action": "already_in_forecast" if already_included else "applied" if applicable else "outside_horizon"})
    arrivals = [0.0] * horizon
    hard_material, soft_material = [0.0] * horizon, [0.0] * horizon
    inbound_log, material_log = [], []
    for item in r.inbound:
        index = (item.expected_at - r.as_of).days - 1
        if 0 <= index < horizon:
            arrivals[index] = _quantity_sum(arrivals[index], item.quantity, "daily inbound quantity")
        inbound_log.append({"id": item.event_id, "date": item.expected_at.isoformat(),
                            "quantity": item.quantity, "source": item.source,
                            "in_horizon": 0 <= index < horizon})
    for item in r.materials:
        uncovered = _uncovered_material(item)
        index = (item.due_at - r.as_of).days - 1
        if 0 <= index < horizon:
            target = hard_material if item.hard else soft_material
            unreserved = float(Decimal(str(item.quantity)) - Decimal(str(item.already_reserved)))
            target[index] = _quantity_sum(target[index], unreserved, "daily material quantity")
        material_log.append({"id": item.requirement_id, "date": item.due_at.isoformat(),
                             "quantity": item.quantity, "uncovered": uncovered,
                             "included_in_forecast": item.included_in_forecast,
                             "already_reserved": item.already_reserved,
                             "hard": item.hard, "source": item.source,
                             "accounting_source": item.accounting_source,
                             "in_horizon": 0 <= index < horizon})
    early, requirements, endings, material_floor, material_early = _simulate_without_order(
        scenarios, free_stock, r.lead_time_days, arrivals, hard_material, soft_material)
    service_level = max((p.service_level for p in policies), default=0.0)
    service_floor = _quantile(requirements, service_level)
    floor_qty = max(material_floor, service_floor)
    quantity = _choose_quantity(requirements, floor_qty, minimum, multiple or r.unit_quantum, r.economics)
    if not _on_lattice(quantity, r.unit_quantum):
        raise ValueError("recommended quantity violates physical unit_quantum")
    count = len(requirements)
    mean = lambda values: sum(values) / count
    lost = mean([e + max(x - quantity, 0) for e, x in zip(early, requirements)])
    ending = mean([e + max(quantity - x, 0) for e, x in zip(endings, requirements)])
    cost = None
    if r.economics:
        cost = r.economics.underage_cost * lost + r.economics.overage_cost * ending
        _number(cost, "expected loss cost")
    if any(x > 0 for x in early):
        warnings.append("shortage_before_new_order_arrival")
    if any(x > 0 for x in material_early):
        warnings.append("hard_material_shortage_before_arrival")
    diagnostics.update({
        "scenario_count": count, "arrival_date": (r.as_of + timedelta(days=r.lead_time_days)).isoformat(),
        "regular_demand_mean": mean([sum(x) for x in scenarios]),
        "dated_material_demand": sum(hard_material) + sum(soft_material),
        "inbound_in_horizon": sum(arrivals),
        "growth": growth_log, "inbound": inbound_log, "materials": material_log,
        "included_growth_ids": list(r.included_growth_ids),
        "post_arrival_requirement_scenarios": requirements,
        "before_arrival_lost_mean": mean(early),
        "before_arrival_lost_max": max(early),
        "before_arrival_material_lost_max": max(material_early),
        "service_floor_quantity": service_floor, "hard_material_floor_quantity": material_floor,
        "minimum_service_level": service_level,
        "post_arrival_no_shortage_fraction": sum(quantity >= x for x in requirements) / count,
        "expected_lost_units": lost, "expected_ending_units": ending,
        "expected_loss_cost": cost, "loss_currency": r.economics.currency if r.economics else None,
        "economic_critical_fraction": r.economics.underage_cost / (r.economics.underage_cost + r.economics.overage_cost) if r.economics else None,
        "min_order_qty_inventory_units": minimum, "order_multiple_inventory_units": multiple,
        "hard_material_scope": "unreserved in-horizon commitments after order arrival in supplied scenarios",
    })
    chosen = "Предварительная потребность" if constraint_unknown else "Рекомендованная партия"
    rationale = "минимум ожидаемых потерь" if r.economics else "явная политика доступности"
    reason = (f"{chosen} {quantity:g} {r.unit}: {rationale}; свободно {free_stock:g}; "
              f"средний регулярный спрос {diagnostics['regular_demand_mean']:g} за {horizon} дн.; "
              f"поступления по датам {sum(arrivals):g}, незарезервированная ведомость {sum(hard_material) + sum(soft_material):g}; "
              f"поставка нового заказа {(r.as_of + timedelta(days=r.lead_time_days)).isoformat()}; "
              f"ожидаемый недопроданный спрос {lost:g}, конечный запас {ending:g}.")
    if any(x > 0 for x in early):
        reason += f" До этой поставки возможен дефицит до {max(early):g} {r.unit}; требуется отдельное ускорение."
    if constraint_unknown:
        reason += " Условия партии не подтверждены: " + ", ".join(constraint_unknown) + "."
    return result("needs_review" if warnings else "ready", None if constraint_unknown else quantity,
                  quantity if constraint_unknown else None, reason,
                  "expedite" if any(x > 0 for x in early) else "normal")


def group_by_supplier(recommendations: Sequence[Recommendation]) -> dict[str, list[Recommendation]]:
    """Keep every line, including needs_data, with its explanation."""
    grouped: dict[str, list[Recommendation]] = {}
    identities = set()
    for row in recommendations:
        identity = (row.supplier_id, row.warehouse_id, row.sku)
        if identity in identities:
            raise ValueError("duplicate recommendation for supplier/warehouse/SKU")
        identities.add(identity)
        _text(row.reason, "recommendation.reason")
        grouped.setdefault(row.supplier_id, []).append(row)
    return grouped


def check_approval(
    recommendations: Sequence[Recommendation], *, current_revision: int,
    expected_revision: int, confirmed_by: str | None, budget: float | None = None,
    currency: str | None = None,
    review_reasons: Mapping[tuple[str, str, str], str] | None = None,
) -> ApprovalCheck:
    """Pure validation only. It neither persists an approval nor sends orders.

    Review keys are (supplier_id, warehouse_id, sku). Missing quantities,
    missing required data, revision conflict, price/currency/budget failures
    cannot be overridden by review prose.
    """
    _integer(current_revision, "current_revision", 0)
    _integer(expected_revision, "expected_revision", 0)
    group_by_supplier(recommendations)
    reasons = []
    if not recommendations:
        reasons.append("empty_order")
    if expected_revision != current_revision:
        reasons.append("revision_conflict")
    if not isinstance(confirmed_by, str) or not confirmed_by.strip():
        reasons.append("human_confirmation_required")
    if budget is not None:
        _number(budget, "budget")
        _text(currency, "budget.currency")
    total, all_prices = Decimal(0), True
    observed_currencies = set()
    for row in recommendations:
        identity = (row.supplier_id, row.warehouse_id, row.sku)
        label = ":".join(identity)
        if row.status not in {"ready", "needs_review", "needs_data"}:
            reasons.append("invalid_status:" + label)
        if row.status == "needs_data" or row.required_fields:
            reasons.append("missing_data:" + label)
        if row.quantity is None:
            reasons.append("unconfirmed_quantity:" + label)
            all_prices = False
            continue
        _number(row.quantity, "recommendation.quantity")
        quantum = row.diagnostics.get("unit_quantum")
        minimum = row.diagnostics.get("min_order_qty_inventory_units")
        multiple = row.diagnostics.get("order_multiple_inventory_units")
        if quantum is None or not _on_lattice(row.quantity, quantum):
            reasons.append("physical_quantity_invalid:" + label)
        if row.quantity > 0 and (minimum is None or multiple is None):
            reasons.append("supplier_constraints_required:" + label)
        elif row.quantity > 0 and (row.quantity < minimum or not _on_lattice(row.quantity, multiple)):
            reasons.append("supplier_quantity_invalid:" + label)
        for field_name in ("hard_material_floor_quantity", "service_floor_quantity"):
            if row.quantity < row.diagnostics.get(field_name, 0):
                reasons.append("quantity_below_" + field_name + ":" + label)
        if row.status == "needs_review":
            rationale = (review_reasons or {}).get(identity)
            if not isinstance(rationale, str) or not rationale.strip():
                reasons.append("review_reason_required:" + label)
        if row.quantity == 0:
            continue
        if row.unit_cost is None or row.price_currency is None:
            all_prices = False
            if budget is not None:
                reasons.append("price_required_for_budget:" + label)
        else:
            _number(row.unit_cost, "recommendation.unit_cost")
            observed_currencies.add(row.price_currency)
            total += Decimal(str(row.quantity)) * Decimal(str(row.unit_cost))
            if budget is not None and row.price_currency != currency:
                reasons.append("currency_mismatch:" + label)
    if len(observed_currencies) > 1:
        all_prices = False
    if not isfinite(float(total)):
        raise ValueError("order cost overflow")
    comparable = all_prices and (budget is None or observed_currencies <= {currency})
    if budget is not None and comparable and total > Decimal(str(budget)):
        reasons.append("budget_exceeded")
    output_currency = currency if budget is not None else next(iter(observed_currencies), None) if len(observed_currencies) <= 1 else None
    return ApprovalCheck(not reasons, tuple(reasons), float(total) if comparable else None,
                         output_currency, current_revision)
