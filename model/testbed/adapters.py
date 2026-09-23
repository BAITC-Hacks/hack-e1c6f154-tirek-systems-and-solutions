"""Public-only JSON boundary to a forecasting function in a fresh subprocess."""
from datetime import date
import json
import math
import subprocess
import sys


def validate_request(request):
    if set(request) != {"schema_version", "as_of", "horizon_days", "items"}:
        raise ValueError("Non-public request fields")
    if request["schema_version"] != "forecast-input-v2":
        raise ValueError("Unknown input schema")
    cutoff = date.fromisoformat(request["as_of"])
    seen = set()
    allowed = {"sku", "unit", "warehouse_id", "supplier_id", "category_raw", "launch_date", "history", "events", "known_promotions", "analogue_history"}
    schemas = {
        "history": {"date", "observed_quantity", "availability_fraction", "complete"},
        "events": {"event_id", "date", "client_id", "quantity"},
        "analogue_history": {"date", "quantity", "source"},
        "known_promotions": {"start_date", "end_date", "announced_at", "planned_multiplier", "source"}}
    for item in request["items"]:
        if set(item) != allowed or item["sku"] in seen:
            raise ValueError("Unexpected item fields or duplicate SKU")
        seen.add(item["sku"])
        if date.fromisoformat(item["launch_date"]) > cutoff:
            raise ValueError("Unlaunched item")
        for name, keys in schemas.items():
            identifiers = set()
            for record in item[name]:
                if set(record) != keys:
                    raise ValueError(f"Unexpected {name} fields")
                timestamp = record["announced_at"] if name == "known_promotions" else record["date"]
                if date.fromisoformat(timestamp) > cutoff:
                    raise ValueError(f"Future observation: {name}")
                for field in ("quantity", "observed_quantity", "availability_fraction", "planned_multiplier"):
                    if field in record:
                        value = record[field]
                        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                            raise ValueError(f"Invalid {field}")
                if name == "history" and record["availability_fraction"] > 1:
                    raise ValueError("Availability outside [0, 1]")
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
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0 for v in values):
            raise ValueError("Forecast values must be finite and nonnegative")
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
