"""Load saved weights and forecast from a fresh compatible sales export."""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
import pandas as pd
from catboost import CatBoostRegressor
from pipeline import HORIZON, FEATURES, read_sales, features_at, baseline_predictions, predict_model


def forecast(sales_path, model_dir):
    model_dir = Path(model_dir)
    manifest = json.loads((model_dir/"manifest.json").read_text(encoding="utf-8"))
    if hashlib.sha256((model_dir/"se-demand-28d.cbm").read_bytes()).hexdigest() != manifest["model_sha256"]:
        raise ValueError("Model checksum differs from manifest.")
    if manifest["features"] != FEATURES or manifest["horizon_days"] != HORIZON:
        raise ValueError("Incompatible feature schema or horizon.")
    daily, audit = read_sales(sales_path)
    origin = daily.columns[-1]
    if origin < pd.Timestamp(manifest["trained_as_of"]):
        raise ValueError("Use a time-appropriate model for historical forecasts; these weights know later data.")
    frame = features_at(daily, origin)
    model = CatBoostRegressor()
    model.load_model(str(model_dir/"se-demand-28d.cbm"))
    prediction = predict_model(model, frame, manifest["candidate"])
    baseline = baseline_predictions(frame)[manifest["baseline_name"]]
    scale = np.maximum(frame["sum84"].to_numpy()/3, 1)
    result = pd.DataFrame({"sku": frame["sku"], "warehouse": audit["warehouse"], "unit": "шт",
                           "data_as_of": str(origin.date()), "forecast_start": str((origin+pd.Timedelta(days=1)).date()),
                           "forecast_end": str((origin+pd.Timedelta(days=HORIZON)).date()),
                           "model_id": manifest["model_id"], "forecast_ml": prediction, "forecast_baseline": baseline})
    for label, residual in zip(("ml_p10_estimate", "ml_p50_estimate", "ml_p90_estimate"), manifest["residual_quantiles"]):
        result[label] = np.maximum(0, prediction+residual*scale)
    method = manifest["preferred_method_on_validation"]
    result["selected_method"] = method
    result["selected_forecast"] = prediction if method == "catboost" else baseline
    result["history_days_since_first_sale"] = frame["age_days"]
    result["scope"] = "observed_sales; availability_and_client_id_unknown"
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sales", required=True, type=Path)
    parser.add_argument("--model-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = forecast(args.sales, args.model_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(args.output, index=False, encoding="utf-8-sig")
    print(f"Saved {len(result)} forecasts to {args.output}")
