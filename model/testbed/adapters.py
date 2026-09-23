"""Public-only JSON boundary to a forecasting function in a fresh subprocess."""
from datetime import date
import json
import math
import subprocess
import sys


MAX_HORIZON_DAYS = 366


def _date(value, field):
    if not isinstance(value, str):
        raise ValueError(f"{field} must be an ISO date YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"Invalid {field}: expected YYYY-MM-DD") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"{field} must be an ISO date YYYY-MM-DD")
    return parsed


def _finite_nonnegative(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value) and value >= 0
    except OverflowError:
        return False


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def validate_request(request):
    if not isinstance(request, dict) or set(request) != {"schema_version", "as_of", "horizon_days", "items"}:
        raise ValueError("Non-public request fields")
    if request["schema_version"] != "forecast-input-v2":
        raise ValueError("Unknown input schema")
    cutoff = _date(request["as_of"], "as_of")
    if type(request["horizon_days"]) is not int or not 1 <= request["horizon_days"] <= MAX_HORIZON_DAYS:
        raise ValueError(f"horizon_days must be an integer in [1, {MAX_HORIZON_DAYS}]")
    if cutoff.toordinal() + request["horizon_days"] > date.max.toordinal():
        raise ValueError("Forecast horizon exceeds supported calendar dates")
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
        if not isinstance(item, dict) or set(item) != allowed:
            raise ValueError("Unexpected item fields")
        if any(not _text(item[key]) for key in ("sku", "unit", "warehouse_id", "supplier_id", "category_raw")):
            raise ValueError("Item identifiers and unit must be nonempty strings")
        if item["sku"] in seen:
            raise ValueError("Unexpected item fields or duplicate SKU")
        seen.add(item["sku"])
        launch = _date(item["launch_date"], "launch_date")
        if launch > cutoff:
            raise ValueError("Unlaunched item")
        for name, keys in schemas.items():
            if not isinstance(item[name], list):
                raise ValueError(f"{name} must be a list")
            identifiers = set()
            previous_date = None
            for record in item[name]:
                if not isinstance(record, dict) or set(record) != keys:
                    raise ValueError(f"Unexpected {name} fields")
                timestamp = record["announced_at"] if name == "known_promotions" else record["date"]
                stamp = _date(timestamp, f"{name} date")
                if stamp > cutoff:
                    raise ValueError(f"Future observation: {name}")
                if name in {"history", "events"} and stamp < launch:
                    raise ValueError(f"{name} before item launch")
                for field in ("quantity", "observed_quantity", "availability_fraction", "planned_multiplier"):
                    if field in record:
                        value = record[field]
                        if not _finite_nonnegative(value):
                            raise ValueError(f"Invalid {field}")
                if name == "history":
                    if type(record["complete"]) is not bool:
                        raise ValueError("history.complete must be boolean")
                    if record["availability_fraction"] > 1:
                        raise ValueError("Availability outside [0, 1]")
                    if previous_date is not None and stamp <= previous_date:
                        raise ValueError("History must be strictly increasing by date")
                    previous_date = stamp
                if name == "events" and any(not _text(record[key]) for key in ("event_id", "client_id")):
                    raise ValueError("Event identifiers must be nonempty strings")
                if name in {"analogue_history", "known_promotions"} and not _text(record["source"]):
                    raise ValueError("Record source must be a nonempty string")
                if name == "known_promotions":
                    start = _date(record["start_date"], "promotion start_date")
                    end = _date(record["end_date"], "promotion end_date")
                    if stamp > start or start > end or record["planned_multiplier"] <= 0:
                        raise ValueError("Promotion requires announced_at <= start_date <= end_date and a positive multiplier")
                if name in {"history", "events"}:
                    identifier = record["event_id"] if name == "events" else record["date"]
                    if identifier in identifiers:
                        raise ValueError(f"Duplicate {name} identifier; deduplicate imports before forecasting")
                    identifiers.add(identifier)


def validate_prediction(request, prediction):
    if not isinstance(prediction, dict) or set(prediction) != {p["sku"] for p in request["items"]}:
        raise ValueError("Forecast must include exactly all requested SKUs")
    for values in prediction.values():
        if not isinstance(values, list) or len(values) != request["horizon_days"]:
            raise ValueError("Forecast must contain horizon_days daily values")
        if any(not _finite_nonnegative(v) for v in values):
            raise ValueError("Forecast values must be finite and nonnegative")
        try:
            horizon_total = math.fsum(values)
        except (OverflowError, ValueError) as exc:
            raise ValueError("Forecast horizon total must be finite") from exc
        if not math.isfinite(horizon_total):
            raise ValueError("Forecast horizon total must be finite")
    return prediction


def call_external(spec, request, timeout=30, kind="forecast"):
    if kind == "forecast":
        validate_request(request)
    # Arguments contain no scenario, seed, generator configuration or truth path.
    result = subprocess.run([sys.executable, "-m", "model.testbed.worker", spec],
                            input=json.dumps(request, allow_nan=False), text=True,
                            capture_output=True, timeout=timeout, check=False)
    if result.returncode:
        raise RuntimeError(f"Adapter exited {result.returncode}: {result.stderr[-1500:]}")
    output = json.loads(result.stdout)
    return validate_prediction(request, output) if kind == "forecast" else output
