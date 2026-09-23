"""Optional seasonal forecast example: public observations only, no fitted seeds.

The policy is declared before measurements: 364-day lag, seven same-weekday
anchors at +/-21 days, 56-day year-over-year growth, 420-day history span,
at least 28 matched growth days and four seasonal anchors per prediction.
The 364-day lag preserves weekdays rather than exact calendar anniversaries.
Growth is a transparent ratio, not a claim to predict unannounced shocks.

Use as an external adapter: ``model.testbed.seasonal:forecast``. The v2.1 CLI
selects this example by default; any compatible forecast function can replace it.
"""
from collections import defaultdict
from datetime import date, timedelta
import math
import statistics


SEASONAL_LAG_DAYS = 364
SMOOTHING_OFFSETS = tuple(range(-21, 22, 7))
GROWTH_WINDOW_DAYS = 56
MIN_HISTORY_SPAN_DAYS = 420
MIN_GROWTH_PAIRS = 28
MIN_SEASONAL_ANCHORS = 4
CLIENT_WINDOW_DAYS = 7
RECURRING_WEEKS = 3
PROJECT_MULTIPLIER = 8


def _project_quantities(item, rows, cutoff):
    """Same client-window heuristic as the control; never inspect event IDs."""
    available = [r["observed_quantity"] / r["availability_fraction"]
                 for r in rows if r["complete"] and r["availability_fraction"] > 0]
    typical = statistics.median(available) if available else 0
    by_client = defaultdict(list)
    for event in item["events"]:
        stamp = date.fromisoformat(event["date"])
        if stamp <= cutoff:
            by_client[event["client_id"]].append((stamp, event["quantity"]))
    excluded = defaultdict(list)
    for events in by_client.values():
        if len({stamp.toordinal() // 7 for stamp, _ in events}) >= RECURRING_WEEKS:
            continue
        ordered = sorted(events, key=lambda event: event[0])
        start_index = 0
        while start_index < len(ordered):
            start = ordered[start_index][0]
            end_index = start_index
            while end_index < len(ordered) and (ordered[end_index][0] - start).days < CLIENT_WINDOW_DAYS:
                end_index += 1
            group = ordered[start_index:end_index]
            if math.fsum(quantity for _, quantity in group) > max(1, typical) * PROJECT_MULTIPLIER:
                for stamp, quantity in group:
                    excluded[stamp].append(quantity)
            start_index = end_index
    return {stamp: math.fsum(quantities) for stamp, quantities in excluded.items()}


def forecast(request):
    """Return daily regular-demand quantities from a public forecast-input-v2.

    Public validation is the adapter boundary's responsibility. Date filters
    here additionally prevent direct callers from using future observations or
    plans that had not been announced at cutoff. Missing and unavailable days
    never become zero demand. A true observed zero remains a valid observation.
    """
    cutoff = date.fromisoformat(request["as_of"])
    output = {}
    for item in request["items"]:
        rows = sorted((r for r in item["history"] if date.fromisoformat(r["date"]) <= cutoff),
                      key=lambda row: row["date"])
        promotions = sorted((p for p in item["known_promotions"]
                             if date.fromisoformat(p["announced_at"]) <= cutoff),
                            key=lambda p: (p["start_date"], p["end_date"], p["announced_at"], p["source"], p["planned_multiplier"]))

        def promotion(stamp):
            factor = math.prod(p["planned_multiplier"] for p in promotions
                               if p["start_date"] <= stamp.isoformat() <= p["end_date"])
            if not math.isfinite(factor) or factor <= 0:
                raise ValueError(f"{item['sku']}: invalid or overflowing promotion factor")
            return factor

        excluded = _project_quantities(item, rows, cutoff)
        normalized, exposures = {}, {}
        for row in rows:
            if not row["complete"] or row["availability_fraction"] <= 0:
                continue
            stamp = date.fromisoformat(row["date"])
            clean_sales = max(0, row["observed_quantity"] - excluded.get(stamp, 0))
            value = clean_sales / row["availability_fraction"] / promotion(stamp)
            if not math.isfinite(value):
                raise ValueError(f"{item['sku']}: normalized demand is not finite")
            normalized[stamp] = value
            exposures[stamp] = row["availability_fraction"]

        recent_start = cutoff - timedelta(days=GROWTH_WINDOW_DAYS - 1)
        recent = [stamp for stamp in normalized if recent_start <= stamp <= cutoff]
        analogue = [r["quantity"] for r in item["analogue_history"]
                    if date.fromisoformat(r["date"]) <= cutoff]
        if recent:
            fallback = (math.fsum(normalized[d] * exposures[d] for d in recent)
                        / math.fsum(exposures[d] for d in recent))
        elif analogue:
            fallback = statistics.mean(analogue)
        else:
            raise ValueError(f"{item['sku']}: no recent complete available observations or explicit analogue; demand is unknown")
        # Explicit analogues can help cold-start products; incomplete/unavailable
        # records do not increase the evidence weight assigned to own history.
        if recent and len(normalized) < 28 and analogue:
            weight = len(normalized) / 28
            fallback = weight * fallback + (1 - weight) * statistics.mean(analogue)

        growth = None
        if rows and (cutoff - date.fromisoformat(rows[0]["date"])).days + 1 >= MIN_HISTORY_SPAN_DAYS:
            pairs = [(stamp, stamp - timedelta(days=SEASONAL_LAG_DAYS))
                     for stamp in recent if stamp - timedelta(days=SEASONAL_LAG_DAYS) in normalized]
            if len(pairs) >= MIN_GROWTH_PAIRS:
                past = math.fsum(normalized[old] for _, old in pairs)
                if past > 0:
                    growth = math.fsum(normalized[new] for new, _ in pairs) / past
                    if not math.isfinite(growth):
                        raise ValueError(f"{item['sku']}: growth ratio is not finite")

        prediction = []
        for day in range(1, request["horizon_days"] + 1):
            stamp = cutoff + timedelta(days=day)
            rate = fallback
            if growth is not None:
                anchor = stamp - timedelta(days=SEASONAL_LAG_DAYS)
                matches = [normalized[anchor + timedelta(days=offset)] for offset in SMOOTHING_OFFSETS
                           if anchor + timedelta(days=offset) in normalized]
                if len(matches) >= MIN_SEASONAL_ANCHORS:
                    rate = statistics.mean(matches) * growth
            value = rate * promotion(stamp)
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{item['sku']}: forecast is not finite and nonnegative")
            prediction.append(value)
        if not math.isfinite(math.fsum(prediction)):
            raise ValueError(f"{item['sku']}: forecast horizon is not finite")
        output[item["sku"]] = prediction
    return output
