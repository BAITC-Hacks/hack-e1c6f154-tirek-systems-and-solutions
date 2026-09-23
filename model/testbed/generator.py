"""Evaluator-owned generator. No object from this module is passed to a model."""
from dataclasses import dataclass
from datetime import date, timedelta
import hashlib
import json
import math
from pathlib import Path
import random

PROTOCOL = json.loads(Path(__file__).with_name("protocol.json").read_text())


def rng_for(*parts):
    digest = hashlib.sha256("|".join(map(str, parts)).encode()).digest()
    return random.Random(int.from_bytes(digest, "big"))


def poisson(rng, mean):
    # Exact sum of independent small Poissons, avoiding exp underflow.
    total = 0
    while mean > 0:
        part = min(mean, 20)
        threshold, product, count = math.exp(-part), 1.0, -1
        while product > threshold:
            product *= rng.random()
            count += 1
        total += count
        mean -= part
    return total


@dataclass
class EvaluationCase:
    scenario: str
    seed: int
    origin: str
    observed: dict
    truth: dict
    projects: dict


def generate(scenario, seed, origin):
    if scenario not in PROTOCOL["scenarios"]:
        raise ValueError(f"Unknown scenario: {scenario}")
    cutoff = date.fromisoformat(origin)
    history, horizon = PROTOCOL["history_days"], PROTOCOL["horizon_days"]
    observed = {"schema_version": "forecast-input-v2", "as_of": origin,
                "horizon_days": horizon, "items": []}
    truth, projects = {}, {}
    # Shared regular streams make stockout/project comparisons paired experiments.
    family = "stable" if scenario in {"stable", "full_stockout", "partial_stockout",
                                      "single_project", "split_project"} else scenario
    for index, base in enumerate((8, 18, 40, 90)):
        sku = f"SKU-{index + 1:03d}"
        regular_rng = rng_for(seed, origin, family, index, "regular")
        censor_rng = rng_for(seed, origin, index, "availability")
        project_rng = rng_for(seed, origin, index, "project")
        client_rng = rng_for(seed, origin, index, "client-identities")
        client_ids = {key: f"client-{client_rng.getrandbits(80):020x}" for key in list(range(11)) + [77, 93]}
        phase = rng_for(seed, origin, index, "phase").uniform(-math.pi, math.pi)
        item = {"sku": sku, "unit": "шт", "warehouse_id": "synthetic-almaty",
                "supplier_id": f"synthetic-supplier-{index % 2 + 1}",
                "category_raw": f"C{index % 2 + 1}", "history": [], "events": [],
                "known_promotions": [], "analogue_history": []}
        cold_start = scenario == "new_product" and index % 2 == 0
        launch = (0 if cold_start else -7) if scenario == "new_product" else -history + 1
        item["launch_date"] = (cutoff + timedelta(days=launch)).isoformat()
        promo_windows = [(-45, -39, 1.8), (5, 11, 1.8)] if scenario in {"known_promotion", "combined"} else []
        for start, end, multiplier in promo_windows:
            item["known_promotions"].append({
                "start_date": (cutoff + timedelta(days=start)).isoformat(),
                "end_date": (cutoff + timedelta(days=end)).isoformat(),
                "announced_at": (cutoff - timedelta(days=60)).isoformat(),
                "planned_multiplier": multiplier, "source": "synthetic-commercial-plan"})
        project_schedule = {}
        if scenario in {"single_project", "split_project", "combined"}:
            for anchor in (-18, 13):
                total_project = int(base * project_rng.uniform(11, 15))
                days = 3 if scenario in {"split_project", "combined"} else 1
                for part in range(days):
                    project_schedule[anchor + part] = total_project // days + (part < total_project % days)
        regular, expectation, project_series = [], [], []
        for offset in range(-history + 1, horizon + 1):
            day = cutoff + timedelta(days=offset)
            weekday = (0.95, 1.03, 1.05, 1.10, 1.07, 0.93, 0.87)[day.weekday()]
            mean = base * weekday
            if scenario in {"seasonal_growth", "combined"}:
                mean *= (1 + 0.38 * math.sin(2 * math.pi * day.timetuple().tm_yday / 365.25 + phase))
                mean *= math.exp(0.0009 * (offset + history))
            for start, end, multiplier in promo_windows:
                if start <= offset <= end:
                    mean *= multiplier
            if scenario in {"demand_shock", "combined"} and 4 <= offset <= 17:
                mean *= 3.5  # No pre-cutoff clue: deliberately unpredictable.
            if scenario == "intermittent":
                probability, mean_size = 0.08, base * 2
                mean = probability * mean_size
                size = math.ceil(math.log(1 - regular_rng.random()) / math.log(1 - 1 / mean_size))
                demand = size if regular_rng.random() < probability else 0
            else:
                demand = poisson(regular_rng, mean)
            recurring = 0
            if scenario == "recurring_client" and day.weekday() == 1:
                recurring = poisson(regular_rng, base * 9)
                demand += recurring
                mean += base * 9
            if scenario == "new_product" and (offset < launch or (cold_start and offset == 0)):
                demand, mean = 0, 0
            project = project_schedule.get(offset, 0)
            availability = 1.0
            if scenario == "full_stockout" and -48 <= offset <= -7:
                availability = 0.0
            elif scenario in {"partial_stockout", "combined"} and -84 <= offset <= 0:
                availability = (0.0, 0.25, 0.5, 1.0)[(offset + 84) % 4]
            # Binomial censoring is independent of latent demand conditional on exposure.
            sold_regular = demand if availability == 1 else sum(censor_rng.random() < availability for _ in range(demand))
            sold_project = project if availability == 1 else sum(censor_rng.random() < availability for _ in range(project))
            regular.append(demand)
            expectation.append(mean)
            project_series.append(project)
            if offset > 0 or offset < launch or (cold_start and offset == 0):
                continue
            stamp = day.isoformat()
            item["history"].append({"date": stamp, "observed_quantity": sold_regular + sold_project,
                                    "availability_fraction": availability, "complete": True})
            # No event is labeled with the evaluator's project/regular classification.
            normal = sold_regular - recurring
            if normal > 0:
                item["events"].append({"event_id": f"{sku}-{stamp}-0", "date": stamp,
                    "client_id": client_ids[day.toordinal() % 11], "quantity": normal})
            if recurring:
                item["events"].append({"event_id": f"{sku}-{stamp}-1", "date": stamp,
                    "client_id": client_ids[77], "quantity": recurring})
            if sold_project:
                pieces = 5 if scenario in {"split_project", "combined"} else 1
                for piece in range(pieces):
                    amount = sold_project // pieces + (piece < sold_project % pieces)
                    item["events"].append({"event_id": f"{sku}-{stamp}-{piece + 2}", "date": stamp,
                        "client_id": client_ids[93], "quantity": amount})
        if scenario == "new_product":
            analog_rng = rng_for(seed, origin, index, "analogue")
            for offset in range(-83, 1):
                item["analogue_history"].append({"date": (cutoff + timedelta(days=offset)).isoformat(),
                    "quantity": poisson(analog_rng, base * 1.12), "source": "synthetic-category-analogue"})
        observed["items"].append(item)
        truth[sku] = {"dates": [(cutoff + timedelta(days=d)).isoformat() for d in range(-history + 1, horizon + 1)],
                      "regular_realized": regular, "regular_expectation": expectation}
        projects[sku] = {"dates": truth[sku]["dates"], "one_off_quantity": project_series}
    return EvaluationCase(scenario, seed, origin, observed, truth, projects)
