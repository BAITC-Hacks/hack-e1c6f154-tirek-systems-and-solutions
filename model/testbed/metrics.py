"""Horizon WAPE weights rows by demand; daily error is a separate diagnostic."""
import math


def aggregate(rows):
    rows = list(rows)
    failed = sum(row["error"] is not None for row in rows)
    result = {"rows": len(rows), "failed_rows": failed,
              "realized_regular_quantity": sum(r["realized_regular_quantity"] for r in rows),
              "expected_regular_quantity": sum(r["expected_regular_quantity"] for r in rows),
              "project_quantity": sum(r["project_quantity"] for r in rows),
              "zero_realized_rows": sum(r["realized_regular_quantity"] == 0 for r in rows)}
    for label, target in (("realized", "realized_regular_quantity"), ("expectation", "expected_regular_quantity")):
        denominator = result[target]
        absolute = None if failed else math.fsum(abs(r["forecast_quantity"] - r[target]) for r in rows)
        result[f"absolute_error_{label}"] = absolute
        result[f"wape_{label}"] = absolute / denominator if absolute is not None and denominator else None
        result[f"bias_{label}"] = None if failed or not denominator else math.fsum(r["forecast_quantity"] - r[target] for r in rows) / denominator
    result["daily_wape_realized"] = None if failed or not result["realized_regular_quantity"] else sum(r["daily_absolute_error"] for r in rows) / result["realized_regular_quantity"]
    result["daily_wape_expectation"] = None if failed or not result["expected_regular_quantity"] else sum(r["daily_absolute_error_expectation"] for r in rows) / result["expected_regular_quantity"]
    return result
