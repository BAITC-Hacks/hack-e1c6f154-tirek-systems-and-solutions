"""Illustrative external-callable baseline. Uses only the forecast input contract."""
from collections import defaultdict
from datetime import date, timedelta
import statistics


def forecast(request):
    cutoff = date.fromisoformat(request["as_of"])
    output = {}
    for item in request["items"]:
        rows = sorted(item["history"], key=lambda row: row["date"])
        clean = [r["observed_quantity"] / r["availability_fraction"] for r in rows
                 if r["availability_fraction"] and r["complete"]]
        typical = statistics.median(clean) if clean else 0
        by_client = defaultdict(list)
        for event in item["events"]:
            by_client[event["client_id"]].append(event)
        excluded = defaultdict(float)
        for events in by_client.values():
            weeks = {date.fromisoformat(e["date"]).toordinal() // 7 for e in events}
            if len(weeks) >= 3:
                continue
            ordered = sorted(events, key=lambda e: e["date"])
            while ordered:
                start = date.fromisoformat(ordered[0]["date"])
                group = [e for e in ordered if (date.fromisoformat(e["date"]) - start).days < 7]
                ordered = ordered[len(group):]
                if sum(e["quantity"] for e in group) > max(1, typical) * 8:
                    for event in group:
                        excluded[event["date"]] += event["quantity"]
        def promotion(stamp):
            factor = 1.0
            for promo in item["known_promotions"]:
                if promo["start_date"] <= stamp <= promo["end_date"]:
                    factor *= promo["planned_multiplier"]
            return factor
        numerator = denominator = 0.0
        for row in rows[-56:]:
            if row["complete"] and row["availability_fraction"]:
                numerator += max(0, row["observed_quantity"] - excluded[row["date"]]) / promotion(row["date"])
                denominator += row["availability_fraction"]
        rate = numerator / denominator if denominator else 0
        if len(rows) < 28 and item["analogue_history"]:
            analogue = statistics.mean(r["quantity"] for r in item["analogue_history"])
            weight = min(1, len(rows) / 28)
            rate = weight * rate + (1 - weight) * analogue
        output[item["sku"]] = [rate * promotion((cutoff + timedelta(days=d)).isoformat())
                               for d in range(1, request["horizon_days"] + 1)]
    return output
