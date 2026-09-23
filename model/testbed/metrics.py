"""Horizon accuracy, daily diagnostics, and failure-safe arithmetic by unit."""
import math


def finite_sum(values):
    """Reject nonrepresentable totals instead of emitting inf into JSON metrics."""
    try:
        total = math.fsum(values)
    except (OverflowError, ValueError, TypeError) as exc:
        raise ValueError("Numeric total is not finite") from exc
    if not math.isfinite(total):
        raise ValueError("Numeric total is not finite")
    return total


def _finite(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _ratio(numerator, denominator):
    if not denominator:
        return None
    value = numerator / denominator
    if not math.isfinite(value):
        raise ValueError("Numeric ratio is not finite")
    return value


def aggregate(rows, horizon_days=28):
    rows = list(rows)
    if type(horizon_days) is not int or horizon_days <= 0:
        raise ValueError("horizon_days must be a positive integer")
    units = sorted({r["unit"] for r in rows if "unit" in r})
    explicit_failures = sum(row["error"] is not None for row in rows)
    result = {"rows": len(rows), "failed_rows": explicit_failures,
              "units": units, "aggregation_error": None,
              "zero_realized_rows": sum(r["realized_regular_quantity"] == 0 for r in rows)}
    target_fields = ("realized_regular_quantity", "expected_regular_quantity", "project_quantity")
    metric_fields = [f"{name}_{label}" for label in ("realized", "expectation")
                     for name in ("absolute_error", "wape", "bias", "mae", "mean_signed_error", "daily_wape", "daily_mae")]
    result.update({field: None for field in (*target_fields, *metric_fields)})
    if len(units) > 1:
        result["aggregation_error"] = "Mixed units cannot be pooled; use groups.unit"
        return result
    try:
        for target in target_fields:
            if any(not _finite(r[target]) or r[target] < 0 for r in rows):
                raise ValueError(f"Invalid nonnegative target: {target}")
            result[target] = finite_sum(r[target] for r in rows)
        for row in rows:
            if row["error"] is None and any(not _finite(row[field]) or row[field] < 0 for field in
                                           ("forecast_quantity", "daily_absolute_error", "daily_absolute_error_expectation")):
                raise ValueError("Invalid numeric forecast or daily error")
        if explicit_failures:
            return result
        calculated = {}
        for label, target, daily in (("realized", "realized_regular_quantity", "daily_absolute_error"),
                                     ("expectation", "expected_regular_quantity", "daily_absolute_error_expectation")):
            denominator = result[target]
            absolute = finite_sum(abs(r["forecast_quantity"] - r[target]) for r in rows)
            signed = finite_sum(r["forecast_quantity"] - r[target] for r in rows)
            daily_absolute = finite_sum(r[daily] for r in rows)
            calculated.update({f"absolute_error_{label}": absolute,
                               f"wape_{label}": _ratio(absolute, denominator),
                               f"bias_{label}": _ratio(signed, denominator),
                               f"mae_{label}": _ratio(absolute, len(rows)),
                               f"mean_signed_error_{label}": _ratio(signed, len(rows)),
                               f"daily_wape_{label}": _ratio(daily_absolute, denominator),
                               f"daily_mae_{label}": _ratio(daily_absolute, len(rows) * horizon_days)})
        result.update(calculated)
    except (ValueError, OverflowError) as exc:
        # Never publish the successfully scored subset of an unrepresentable run.
        # evaluate also stamps the affected saved rows with this failure reason.
        result["aggregation_error"] = f"Numeric aggregation failure: {exc}"
        result["failed_rows"] = len(rows)
    return result
