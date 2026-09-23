"""Horizon WAPE and explicit, JSON-safe handling of numeric failures."""
import math


def checked_sum(values, label="quantity"):
    """Sum finite JSON numbers, raising ValueError instead of leaking inf/NaN.

    Signed numbers are permitted for bias. Callers validating quantities must
    check nonnegativity separately. Arbitrarily large Python integers are also
    rejected when they cannot be represented by the floating-point evaluator.
    """
    def validated():
        for value in values:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{label}: expected a finite number")
            try:
                finite = math.isfinite(value)
            except OverflowError as exc:
                raise ValueError(f"{label}: number outside finite range") from exc
            if not finite:
                raise ValueError(f"{label}: non-finite number")
            yield value

    try:
        total = math.fsum(validated())
    except (OverflowError, TypeError) as exc:
        raise ValueError(f"{label}: sum outside finite range") from exc
    if not math.isfinite(total):
        raise ValueError(f"{label}: non-finite sum")
    return total


def aggregate(rows):
    """Retain coverage and representable quantities even when arithmetic fails.

    Any numeric failure invalidates all ratio metrics for this group, rather
    than silently scoring a successful subset. `failed_rows` counts adapter
    failures only; `numerical_failure` also covers overflow across valid rows.
    """
    rows = list(rows)
    failed = sum(row["error"] is not None for row in rows)
    numerical_errors = []

    def guarded(label, operation):
        try:
            value = operation()
            if value is not None and not math.isfinite(value):
                raise ValueError("non-finite result")
            return value
        except (ValueError, OverflowError, TypeError, ZeroDivisionError) as exc:
            numerical_errors.append(f"{label}: {exc}")
            return None

    def quantity_sum(field):
        def values():
            for row in rows:
                value = row[field]
                if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                    raise ValueError("expected a nonnegative number")
                yield value
        return guarded(field, lambda: checked_sum(values(), field))

    result = {"rows": len(rows), "failed_rows": failed,
              "realized_regular_quantity": quantity_sum("realized_regular_quantity"),
              "expected_regular_quantity": quantity_sum("expected_regular_quantity"),
              "project_quantity": quantity_sum("project_quantity"),
              "zero_realized_rows": sum(r["realized_regular_quantity"] == 0 for r in rows)}
    ratio_fields = []
    for label, target, daily_field in (
        ("realized", "realized_regular_quantity", "daily_absolute_error"),
        ("expectation", "expected_regular_quantity", "daily_absolute_error_expectation"),
    ):
        denominator = result[target]
        absolute = bias_sum = daily_sum = None
        if not failed:
            def differences():
                for row in rows:
                    forecast = row["forecast_quantity"]
                    actual = row[target]
                    for value in (forecast, actual):
                        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                            raise ValueError("expected a nonnegative number")
                        checked_sum([value], "row quantity")
                    yield forecast - actual
            absolute = guarded(f"absolute_error_{label}",
                               lambda: checked_sum((abs(v) for v in differences()), f"absolute_error_{label}"))
            bias_sum = guarded(f"bias_sum_{label}", lambda: checked_sum(differences(), f"bias_sum_{label}"))
            daily_sum = quantity_sum(daily_field)
        result[f"absolute_error_{label}"] = absolute
        for field, numerator in ((f"wape_{label}", absolute),
                                 (f"bias_{label}", bias_sum),
                                 (f"daily_wape_{label}", daily_sum)):
            ratio_fields.append(field)
            result[field] = (guarded(field, lambda n=numerator: n / denominator)
                             if not failed and numerator is not None and denominator else None)
    result["numerical_failure"] = bool(numerical_errors)
    result["numerical_errors"] = numerical_errors
    if numerical_errors:
        for field in ratio_fields:
            result[field] = None
    return result
