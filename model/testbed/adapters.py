"""Public-only JSON boundary to a forecasting function in a fresh subprocess."""
from collections import defaultdict
from datetime import date, timedelta
import json
import math
import subprocess
import sys

from .metrics import checked_sum


def _fields(value, expected, label):
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError(f"Unexpected {label} fields")


def _text(value, label):
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{label} must be a nonempty normalized string")
    return value


def _date(value, label):
    if not isinstance(value, str):
        raise ValueError(f"{label} must be an ISO YYYY-MM-DD date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"Invalid {label} date") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"{label} must be an ISO YYYY-MM-DD date")
    return parsed


def _number(value, label, positive=False):
    checked_sum([value], label)
    if value < 0 or (positive and value == 0):
        raise ValueError(f"Invalid {label}: expected {'positive' if positive else 'nonnegative'} number")
    return value


def _horizon(value):
    if type(value) is not int or value <= 0:
        raise ValueError("horizon_days must be a positive integer")
    return value


def validate_request(request):
    """Validate the complete public packet without relying on record order.

    Missing calendar days are permitted and are not interpreted as zero sales.
    Every event must have an observed history row, and the documents for each
    supplied day must add up to that row's observed quantity (also on incomplete
    days). Raw or partial partner imports must be reconciled before this boundary.
    """
    _fields(request, {"schema_version", "as_of", "horizon_days", "items"}, "request")
    if request["schema_version"] != "forecast-input-v2":
        raise ValueError("Unknown input schema")
    cutoff = _date(request["as_of"], "as_of")
    horizon = _horizon(request["horizon_days"])
    try:
        cutoff + timedelta(days=horizon)
    except OverflowError as exc:
        raise ValueError("Forecast horizon exceeds supported calendar dates") from exc
    if not isinstance(request["items"], list) or not request["items"]:
        raise ValueError("items must be a nonempty list")
    seen = set()
    allowed = {"sku", "unit", "warehouse_id", "supplier_id", "category_raw", "launch_date", "history", "events", "known_promotions", "analogue_history"}
    schemas = {
        "history": {"date", "observed_quantity", "availability_fraction", "complete"},
        "events": {"event_id", "date", "client_id", "quantity"},
        "analogue_history": {"date", "quantity", "source"},
        "known_promotions": {"start_date", "end_date", "announced_at", "planned_multiplier", "source"}}
    for item in request["items"]:
        _fields(item, allowed, "item")
        for field in ("sku", "unit", "warehouse_id", "supplier_id", "category_raw"):
            _text(item[field], field)
        if item["sku"] in seen:
            raise ValueError("Duplicate SKU")
        seen.add(item["sku"])
        launch = _date(item["launch_date"], "launch_date")
        if launch > cutoff:
            raise ValueError("Unlaunched item")
        history = {}
        event_quantities = defaultdict(list)
        for name, keys in schemas.items():
            if not isinstance(item[name], list):
                raise ValueError(f"{name} must be a list")
            identifiers = set()
            for record in item[name]:
                _fields(record, keys, name)
                if name == "known_promotions":
                    start = _date(record["start_date"], "promotion start_date")
                    end = _date(record["end_date"], "promotion end_date")
                    announced = _date(record["announced_at"], "promotion announced_at")
                    if start > end:
                        raise ValueError("Promotion start_date must not follow end_date")
                    if announced > cutoff:
                        raise ValueError("Future observation: known_promotions")
                    _number(record["planned_multiplier"], "planned_multiplier", positive=True)
                    _text(record["source"], "promotion source")
                    identifier = (start, end, record["source"])
                else:
                    stamp = _date(record["date"], name + " date")
                    if stamp > cutoff:
                        raise ValueError(f"Future observation: {name}")
                    if name in {"history", "events"} and stamp < launch:
                        raise ValueError(f"Observation before launch: {name}")
                    if name == "history":
                        _number(record["observed_quantity"], "observed_quantity")
                        _number(record["availability_fraction"], "availability_fraction")
                        if record["availability_fraction"] > 1:
                            raise ValueError("Availability outside [0, 1]")
                        if type(record["complete"]) is not bool:
                            raise ValueError("complete must be boolean")
                        if record["availability_fraction"] == 0 and record["observed_quantity"] != 0:
                            raise ValueError("Positive observed quantity with zero availability")
                        identifier = record["date"]
                        history[identifier] = record
                    elif name == "events":
                        _number(record["quantity"], "event quantity")
                        _text(record["client_id"], "client_id")
                        identifier = _text(record["event_id"], "event_id")
                        event_quantities[record["date"]].append(record["quantity"])
                    else:
                        _number(record["quantity"], "analogue quantity")
                        _text(record["source"], "analogue source")
                        identifier = (record["date"], record["source"])
                if identifier in identifiers:
                    raise ValueError(f"Duplicate {name} identifier; deduplicate imports before forecasting")
                identifiers.add(identifier)
        if set(event_quantities) - set(history):
            raise ValueError("Event date has no matching history observation")
        for stamp, record in history.items():
            total = checked_sum(event_quantities.get(stamp, []), "event day quantity")
            if not math.isclose(total, record["observed_quantity"], rel_tol=1e-10, abs_tol=1e-8):
                raise ValueError("Event quantities do not match observed history quantity")


def validate_prediction(request, prediction):
    if not isinstance(prediction, dict) or set(prediction) != {p["sku"] for p in request["items"]}:
        raise ValueError("Forecast must include exactly all requested SKUs")
    horizon = _horizon(request["horizon_days"])
    for sku, values in prediction.items():
        if not isinstance(values, list) or len(values) != horizon:
            raise ValueError("Forecast must contain horizon_days daily values")
        for value in values:
            _number(value, "forecast value")
        checked_sum(values, f"forecast horizon for {sku}")
    return prediction


def _unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON response field: {key}")
        result[key] = value
    return result


def call_external(spec, request, timeout=30, kind="forecast"):
    if kind == "forecast":
        validate_request(request)
    # Arguments contain no scenario, seed, generator configuration or truth path.
    result = subprocess.run([sys.executable, "-m", "model.testbed.worker", spec],
                            input=json.dumps(request, allow_nan=False), text=True,
                            capture_output=True, timeout=timeout, check=False)
    if result.returncode:
        raise RuntimeError(f"Adapter exited {result.returncode}: {result.stderr[-1500:]}")
    output = json.loads(result.stdout, object_pairs_hook=_unique_json_object)
    return validate_prediction(request, output) if kind == "forecast" else output
