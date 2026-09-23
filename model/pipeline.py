"""Point-in-time sales features for a 28-day forecast; no order or UI logic."""
from pathlib import Path
import hashlib
import numpy as np
import pandas as pd
from openpyxl import load_workbook

HORIZON = 28
CAT_FEATURES = ["sku"]
FEATURES = ["sku", "age_days", "month_sin", "month_cos", "target_month",
            "sum7", "sum14", "sum28", "sum56", "sum84", "week2", "week3", "week4",
            "nonzero28", "nonzero84", "std28", "max28", "days_since_sale",
            "trend28", "robust_sum28", "large_day_excess28", "prior_year28", "seasonal_available"]


def read_sales(path):
    path = Path(path)
    workbook = load_workbook(path, read_only=True, data_only=True)
    sheet = workbook.worksheets[0]
    headers = next(sheet.iter_rows(values_only=True))
    expected = ["Дата", "Номер", "Документ", "Код", "Номенклатура", "Ед.", "Склад", "Количество"]
    if [str(value).strip() for value in headers[:8]] != expected:
        workbook.close()
        raise ValueError("Unexpected column order; use the documented SE transaction export.")
    rows = list(sheet.iter_rows(min_row=2, max_col=8, values_only=True))
    workbook.close()
    frame = pd.DataFrame(rows, columns=["date", "document_id", "document", "sku", "name", "unit", "warehouse", "quantity"])
    frame["date"] = pd.to_datetime(frame["date"], format="mixed", dayfirst=True, errors="coerce").dt.normalize()
    frame["quantity"] = pd.to_numeric(frame["quantity"], errors="coerce")
    dated = frame["date"].notna() & frame["sku"].notna()
    positive = frame["quantity"].gt(0) & np.isfinite(frame["quantity"])
    invoice = frame["document"].astype(str).str.startswith("Расходная накладная")
    period = frame["date"].ge("2025-01-01")
    use = dated & positive & invoice & period
    clean = frame.loc[use].copy()
    clean["sku"] = clean["sku"].map(lambda x: str(int(x)) if isinstance(x, (int, float)) and x == int(x) else str(x).strip())
    if clean.empty:
        raise ValueError("No positive invoice sales from 2025 onward.")
    if set(clean["unit"]) != {"шт"} or clean["warehouse"].nunique() != 1:
        raise ValueError("This trained SE adapter requires one warehouse and units 'шт'; do not pool unlike units.")
    daily = clean.groupby(["sku", "date"])["quantity"].sum().unstack(fill_value=0)
    dates = pd.date_range("2025-01-01", clean["date"].max(), freq="D")
    daily = daily.reindex(columns=dates, fill_value=0).sort_index().astype(float)
    audit = {
        "filename": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "input_rows": len(frame), "used_rows": int(use.sum()), "excluded_rows": int((~use).sum()),
        "negative_rows": int(frame["quantity"].lt(0).sum()),
        "undated_or_missing_sku_rows": int((~dated).sum()),
        "non_invoice_rows": int((dated & ~invoice).sum()),
        "sku_count": len(daily), "warehouse": str(clean["warehouse"].iloc[0]), "unit": "шт",
        "first_positive_sale": str(clean["date"].min().date()), "data_as_of": str(dates[-1].date()),
        "observed_positive_quantity": float(clean["quantity"].sum()),
        "client_id_available": False, "daily_availability_available": False,
        "last_day_completeness": "unverified; final day excluded from evaluation targets",
        "quantity_policy": "positive invoice quantities; returns/corrections are not abs() and are not latent demand",
    }
    return daily, audit


def features_at(daily, origin, include_target=False):
    origin = pd.Timestamp(origin).normalize()
    if origin not in daily.columns:
        raise ValueError("Forecast origin must be a date present in the input calendar.")
    pos = daily.columns.get_loc(origin)
    if pos < 83:
        raise ValueError("At least 84 calendar days are required.")
    values = daily.to_numpy()
    hist = values[:, :pos + 1]
    seen = (hist > 0).any(axis=1)
    history = hist[seen]
    skus = daily.index[seen]
    first = np.argmax(history > 0, axis=1)
    last = history.shape[1] - 1 - np.argmax((history > 0)[:, ::-1], axis=1)
    window = history[:, -84:]
    recent = window[:, -28:]
    target_mid = origin + pd.Timedelta(days=14)
    data = {"sku": skus.astype(str), "age_days": pos - first,
            "month_sin": np.sin(2 * np.pi * target_mid.dayofyear / 365.25),
            "month_cos": np.cos(2 * np.pi * target_mid.dayofyear / 365.25),
            "target_month": target_mid.month}
    for size in (7, 14, 28, 56, 84):
        data[f"sum{size}"] = window[:, -size:].sum(axis=1)
    for week in (2, 3, 4):
        data[f"week{week}"] = window[:, -7 * week:-7 * (week - 1)].sum(axis=1)
    data.update(nonzero28=(recent > 0).sum(axis=1), nonzero84=(window > 0).sum(axis=1),
                std28=recent.std(axis=1), max28=recent.max(axis=1), days_since_sale=pos-last)
    data["trend28"] = (data["sum28"] + 1) / (data["sum56"] - data["sum28"] + 1)
    # Only a feature: suspicious large days are NOT silently removed from actual labels.
    cap = np.maximum(1, 3 * np.quantile(window, .95, axis=1))
    cap[(window > 0).sum(axis=1) < 10] = np.inf
    robust = np.minimum(recent, cap[:, None]).sum(axis=1)
    data["robust_sum28"] = robust
    data["large_day_excess28"] = data["sum28"] - robust
    start, end = pos + 1 - 364, pos + HORIZON + 1 - 364
    seasonal = history[:, start:end].sum(axis=1) if start >= 0 else np.full(len(skus), np.nan)
    data["prior_year28"] = seasonal
    data["seasonal_available"] = np.isfinite(seasonal).astype(int)
    result = pd.DataFrame(data)
    result["origin"] = origin
    result["label_end"] = origin + pd.Timedelta(days=HORIZON)
    if include_target:
        if pos + HORIZON >= len(daily.columns):
            raise ValueError("Incomplete 28-day target window.")
        result["target"] = values[seen, pos + 1:pos + HORIZON + 1].sum(axis=1)
    return result


def baseline_predictions(frame):
    recent = frame["sum28"].to_numpy()
    mean56 = frame["sum56"].to_numpy() / 2
    seasonal = frame["prior_year28"].to_numpy()
    return {"recent28": recent, "mean56": mean56,
            "seasonal_blend": np.where(np.isfinite(seasonal), .5 * seasonal + .5 * mean56, mean56)}


def metrics(actual, predicted):
    actual, predicted = np.asarray(actual, dtype=float), np.asarray(predicted, dtype=float)
    total = float(actual.sum())
    return {"sku_count": len(actual), "actual_quantity": total,
            "mae_units": float(np.mean(np.abs(predicted-actual))),
            "wape": float(np.abs(predicted-actual).sum()/total) if total else None,
            "bias": float((predicted-actual).sum()/total) if total else None,
            "predicted_quantity": float(predicted.sum())}


def aggregate_metrics(folds, key):
    reports = [fold[key] for fold in folds]
    count = sum(row["sku_count"] for row in reports)
    actual = sum(row["actual_quantity"] for row in reports)
    predicted = sum(row["predicted_quantity"] for row in reports)
    absolute_error = sum(row["mae_units"] * row["sku_count"] for row in reports)
    return {"sku_windows": count, "mae_units": absolute_error / count,
            "wape": absolute_error / actual if actual else None,
            "bias": (predicted-actual) / actual if actual else None}


def predict_model(model, frame, loss):
    kind = "Exponent" if loss in ("Poisson", "ScaledPoisson") else "RawFormulaVal"
    prediction = np.asarray(model.predict(frame[FEATURES], prediction_type=kind), dtype=float)
    if not np.isfinite(prediction).all():
        raise ValueError("Model produced non-finite forecasts.")
    if loss.startswith("Scaled"):
        prediction *= np.maximum(frame["sum84"].to_numpy()/3, 10)
    return np.maximum(0, prediction)
