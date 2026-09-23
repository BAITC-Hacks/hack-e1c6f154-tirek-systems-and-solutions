"""Exposure-aware Poisson regression and client-window cleaning.

The public request is deliberately independent of generator/scorer modules. All
coefficients are refitted from observations at or before as_of. No synthetic
scenario identifier, random seed, hidden target or future outcome is accepted.
"""
from collections import defaultdict
from datetime import date, timedelta
import math

import numpy as np

VERSION = "regular-poisson-v1"
CANDIDATES = ("mean56", "mean182", "weekday365", "trend182", "seasonal560", "median365")


def _dates(values):
    return np.array([date.fromisoformat(v).toordinal() for v in values], dtype=float)


def _promotions(stamps, promotions, cutoff):
    factors = np.ones(len(stamps))
    seen = set()
    for promotion in promotions:
        for field in ("announced_at", "start_date", "end_date"):
            date.fromisoformat(promotion[field])
        if promotion["announced_at"] > cutoff:
            raise ValueError("Promotion was not known at forecast cutoff")
        key = (promotion["source"], promotion["start_date"], promotion["end_date"])
        if key in seen:
            raise ValueError("Duplicate promotion")
        seen.add(key)
        multiplier = promotion["planned_multiplier"]
        if isinstance(multiplier, bool) or not isinstance(multiplier, (int, float)) or not np.isfinite(multiplier) or multiplier <= 0:
            raise ValueError("Promotion multiplier must be positive")
        if promotion["start_date"] > promotion["end_date"]:
            raise ValueError("Reversed promotion interval")
        for i, stamp in enumerate(stamps):
            if promotion["start_date"] <= stamp <= promotion["end_date"]:
                factors[i] *= multiplier
    return factors


def clean_history(item, cutoff):
    """Exclude unusual excess over a client's recurring weekly volume.

    This is an explicit heuristic, not a verified label. Original rows survive
    outside the model. With absent client events no client exclusion is invented.
    """
    rows = sorted(item["history"], key=lambda r: r["date"])
    stamps = [r["date"] for r in rows]
    if len(set(stamps)) != len(stamps) or any(s > cutoff for s in stamps):
        raise ValueError("Duplicate or future daily observation")
    observed = np.asarray([r["observed_quantity"] for r in rows], dtype=float)
    availability = np.asarray([r["availability_fraction"] for r in rows], dtype=float)
    if any(type(r["complete"]) is not bool for r in rows):
        raise ValueError("complete must be boolean")
    complete = np.asarray([r["complete"] for r in rows], dtype=bool)
    if (not np.isfinite(observed).all() or (observed < 0).any()
            or not np.isfinite(availability).all()
            or ((availability < 0) | (availability > 1)).any()):
        raise ValueError("Invalid observed sales or availability")
    reliable = complete & (availability >= 0.5)
    values = observed[reliable] / availability[reliable]
    positive = values[values > 0]
    typical = float(np.median(positive)) if len(positive) else 0.0
    by_client, event_ids = defaultdict(list), set()
    by_date = dict(zip(stamps, observed))
    event_totals = defaultdict(float)
    for event in item.get("events", []):
        quantity = event["quantity"]
        if (event["date"] not in by_date or event["date"] > cutoff
                or event["event_id"] in event_ids or not np.isfinite(quantity) or quantity < 0):
            raise ValueError("Invalid, duplicate or future sales event")
        event_ids.add(event["event_id"])
        event_totals[event["date"]] += quantity
        if event.get("client_id") is not None and quantity > 0:
            by_client[event["client_id"]].append(event)
    if any(q > by_date[s] + 1e-8 for s, q in event_totals.items()):
        raise ValueError("Event quantities exceed observed sales")
    excluded, windows = defaultdict(float), []
    for client, events in by_client.items():
        ordered = sorted(events, key=lambda e: e["date"])
        position, groups = 0, []
        while position < len(ordered):
            start = date.fromisoformat(ordered[position]["date"])
            end = position + 1
            while end < len(ordered) and (date.fromisoformat(ordered[end]["date"]) - start).days < 7:
                end += 1
            groups.append(ordered[position:end])
            position = end
        totals = [sum(e["quantity"] for e in group) for group in groups]
        normal_volume = float(np.median(totals)) if len(totals) >= 3 else 0.0
        # Scan overlapping windows too: a small earlier event must not shift a
        # fixed bucket boundary so that two large invoices escape detection.
        sliding = []
        for stamp in sorted({e["date"] for e in ordered}):
            start = date.fromisoformat(stamp)
            group = [e for e in ordered if 0 <= (date.fromisoformat(e["date"]) - start).days < 7]
            sliding.append(group)
        removed_events = defaultdict(float)
        for group in sorted(sliding, key=lambda g: sum(e["quantity"] for e in g), reverse=True):
            quantity = sum(e["quantity"] - removed_events[e["event_id"]] for e in group)
            excess = max(0.0, quantity - normal_volume)
            if excess > 8 * max(typical, 1):
                # Preserve the client's ordinary volume. Remove excess from the
                # largest events first so split documents cannot avoid grouping.
                remaining = excess
                for event in sorted(group, key=lambda e: e["quantity"], reverse=True):
                    amount = min(remaining, event["quantity"] - removed_events[event["event_id"]])
                    excluded[event["date"]] += amount
                    removed_events[event["event_id"]] += amount
                    remaining -= amount
                windows.append({"client_id": client, "start": group[0]["date"],
                                "quantity": excess, "retained_normal_quantity": normal_volume,
                                "reason": "unusual_client_window_excess"})
    cleaned = observed - np.asarray([excluded[s] for s in stamps])
    exposure = availability * complete
    if ((observed > 0) & (availability == 0)).any():
        raise ValueError("Sales contradict zero availability")
    return stamps, np.maximum(cleaned, 0), exposure, windows


def design(days, origin, method):
    days = np.asarray(days)
    columns = [np.ones(len(days))]
    if method in ("trend182", "seasonal560"):
        columns.append((days - origin) / 365.25)
    if method == "seasonal560":
        phase = 2 * np.pi * days / 365.25
        columns.extend((np.sin(phase), np.cos(phase)))
    if method in ("weekday365", "trend182", "seasonal560"):
        weekday = (days.astype(int) - 1) % 7
        columns.extend((weekday == k).astype(float) for k in range(6))
    return np.column_stack(columns)


def fit_predict(days, quantities, exposure, future, method):
    """Regularized Poisson GLM with known exposure as an offset, fitted by IRLS."""
    if method not in CANDIDATES:
        raise ValueError("Unknown forecasting method")
    window = int("".join(c for c in method if c.isdigit()))
    origin = future[0] - 1
    mask = (days > origin - window) & (days <= origin) & (exposure > 0)
    x, y, e = days[mask], quantities[mask], exposure[mask]
    if not len(x):
        return np.zeros(len(future))
    rate = y.sum() / e.sum()
    if rate == 0:
        return np.zeros(len(future))
    if method in ("mean56", "mean182"):
        # The intercept-only MLE is analytic; avoid exp(log(rate)) round-off
        # that could unnecessarily cross a discrete procurement boundary.
        return np.full(len(future), rate)
    if method == "median365":
        # A point forecast for absolute horizon error is a median, not a mean.
        # Retain zero-demand windows: dropping them would bias intermittent SKU.
        totals = []
        length = len(future)
        for end in range(int(origin), int(max(origin - 365, x.min())) + length - 2, -7):
            period = (x > end - length) & (x <= end)
            if period.sum() == length and (e[period] > 0).all():
                totals.append(float(np.sum(y[period] / e[period])))
        if len(totals) >= 4:
            return np.full(len(future), float(np.median(totals)) / length)
        return np.full(len(future), rate)
    # Too little data cannot identify trend/annual seasonality.
    if (method == "seasonal560" and np.ptp(x) < 365) or len(x) < 28:
        return np.full(len(future), rate)
    X, future_X = design(x, origin, method), design(future, origin, method)
    beta = np.zeros(X.shape[1])
    beta[0] = math.log(rate)
    penalty = np.full(len(beta), 1.0)
    penalty[0] = 0
    def objective(b):
        eta = np.clip(X @ b, -20, 20)
        return float(np.sum(e * np.exp(eta) - y * eta) + 0.5 * np.sum(penalty * b * b))
    for _ in range(40):
        mu = e * np.exp(np.clip(X @ beta, -20, 20))
        gradient = X.T @ (mu - y) + penalty * beta
        hessian = X.T @ (mu[:, None] * X) + np.diag(penalty + 1e-8)
        step = np.linalg.solve(hessian, gradient)
        scale, previous = 1.0, objective(beta)
        while scale > 1 / 1024 and objective(beta - scale * step) > previous:
            scale *= 0.5
        beta -= scale * step
        if np.max(np.abs(scale * step)) < 1e-7:
            break
    return np.exp(np.clip(future_X @ beta, -20, 20))


def forecast_with_audit(request, method="adaptive"):
    cutoff, horizon = request["as_of"], request["horizon_days"]
    if request.get("schema_version") != "forecast-input-v2" or set(request) != {"schema_version", "as_of", "horizon_days", "items"}:
        raise ValueError("Expected public observed-only input schema")
    if not isinstance(horizon, int) or isinstance(horizon, bool) or horizon <= 0:
        raise ValueError("Positive integer horizon required")
    origin = date.fromisoformat(cutoff).toordinal()
    future = np.arange(origin + 1, origin + horizon + 1, dtype=float)
    output, audits = {}, {}
    for item in request["items"]:
        expected_keys = {"sku", "unit", "warehouse_id", "supplier_id", "category_raw", "launch_date",
                         "history", "events", "known_promotions", "analogue_history"}
        if set(item) != expected_keys:
            raise ValueError("Unexpected item fields in observed-only contract")
        schemas = {"history": {"date", "observed_quantity", "availability_fraction", "complete"},
                   "events": {"event_id", "date", "client_id", "quantity"},
                   "known_promotions": {"start_date", "end_date", "announced_at", "planned_multiplier", "source"},
                   "analogue_history": {"date", "quantity", "source"}}
        for name, keys in schemas.items():
            if not isinstance(item[name], list) or any(set(row) != keys for row in item[name]):
                raise ValueError("Unexpected observed record fields: " + name)
            for row in item[name]:
                for field in ("date", "start_date", "end_date", "announced_at"):
                    if field in row:
                        date.fromisoformat(row[field])
                if "date" in row and row["date"] > cutoff:
                    raise ValueError("Future observation: " + name)
                for field in ("quantity", "observed_quantity", "availability_fraction", "planned_multiplier"):
                    if field in row and (isinstance(row[field], bool) or not isinstance(row[field], (int, float))
                                         or not np.isfinite(row[field]) or row[field] < 0):
                        raise ValueError("Invalid observed quantity: " + field)
        sku = item["sku"]
        if sku in output:
            raise ValueError("Duplicate SKU")
        if item.get("launch_date", cutoff) > cutoff:
            raise ValueError("SKU not launched at forecast cutoff")
        stamps, clean, available, excluded = clean_history(item, cutoff)
        days = _dates(stamps)
        promotions = item.get("known_promotions", [])
        exposure = available * _promotions(stamps, promotions, cutoff)
        future_stamps = [date.fromordinal(int(d)).isoformat() for d in future]
        future_factor = _promotions(future_stamps, promotions, cutoff)
        methods = list(CANDIDATES)
        # Candidate choice itself uses only completed earlier validation windows.
        losses, validation_windows = {name: 0.0 for name in methods}, 0
        if method == "adaptive" and len(days) >= 168:
            for offset in (84, 56, 28):
                split = origin - offset
                train = days <= split
                valid = (days > split) & (days <= split + 28)
                if train.sum() < 84 or available[valid].sum() < 7:
                    continue
                # Re-clean only pre-split events, avoiding future recurrence labels.
                old_cutoff = date.fromordinal(split).isoformat()
                old_item = {**item, "history": [r for r in item["history"] if r["date"] <= old_cutoff],
                            "events": [r for r in item.get("events", []) if r["date"] <= old_cutoff]}
                old_stamps, old_clean, old_available, _ = clean_history(old_item, old_cutoff)
                old_promos = [p for p in promotions if p["announced_at"] <= old_cutoff]
                old_exposure = old_available * _promotions(old_stamps, old_promos, old_cutoff)
                horizon_days = np.arange(split + 1, split + 29, dtype=float)
                past_factors = _promotions([date.fromordinal(int(d)).isoformat() for d in horizon_days], old_promos, old_cutoff)
                positions = (days[valid] - split - 1).astype(int)
                # Selection scores only observed regular estimates. It does not
                # turn missing stockout demand into a verified historical target.
                for name in methods:
                    predicted = fit_predict(_dates(old_stamps), old_clean, old_exposure, horizon_days, name)
                    observed_prediction = (predicted * past_factors)[positions] * available[valid]
                    losses[name] += abs(float(observed_prediction.sum() - clean[valid][available[valid] > 0].sum()))
                validation_windows += 1
        selected = min(methods, key=losses.get) if method == "adaptive" and validation_windows else ("mean182" if method == "adaptive" else method)
        prediction = fit_predict(days, clean, exposure, future, selected)
        analogue = item.get("analogue_history", [])
        if available.sum() < 28 and analogue:
            if any(r["date"] > cutoff or not np.isfinite(r["quantity"]) or r["quantity"] < 0 for r in analogue):
                raise ValueError("Invalid/future category analogue")
            prior = float(np.mean([r["quantity"] for r in analogue]))
            weight = min(1.0, float(available.sum()) / 28)
            prediction = weight * prediction + (1 - weight) * prior
        elif available.sum() == 0:
            raise ValueError("No observed exposure or category analogue: demand is unknown")
        prediction *= future_factor
        if not np.isfinite(prediction).all() or (prediction < 0).any():
            raise ValueError("Nonfinite model output")
        output[sku] = prediction.tolist()
        audits[sku] = {"model": VERSION, "selected_method": selected, "validation_windows": validation_windows,
                       "method_losses": losses if validation_windows else None,
                       "excluded_client_windows": excluded,
                       "available_equivalent_days": float(available.sum()),
                       "zero_exposure_days": int(np.sum(available == 0)),
                       "demand_correction": "Poisson offset: availability × announced promotion"}
    return output, audits


def forecast(request):
    return forecast_with_audit(request)[0]


def forecast_seasonal(request):
    return forecast_with_audit(request, "seasonal560")[0]


def forecast_long_mean(request):
    return forecast_with_audit(request, "mean182")[0]
