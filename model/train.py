"""Train CatBoost, select on validation, evaluate two subsequent time windows."""
import argparse
import json
import platform
from datetime import datetime, timezone
from pathlib import Path
import time
import catboost
from catboost import CatBoostRegressor
import numpy as np
import pandas as pd
from pipeline import HORIZON, CAT_FEATURES, FEATURES, read_sales, features_at, baseline_predictions, metrics, predict_model, aggregate_metrics


def fit(frame, cutoff, loss, iterations, threads):
    train = frame.loc[frame["label_end"] <= cutoff]
    if len(train) < 500:
        raise ValueError("Insufficient training rows before cutoff.")
    objective = {"ScaledPoisson": "Poisson", "ScaledRMSE": "RMSE"}.get(loss, loss)
    model = CatBoostRegressor(loss_function=objective, iterations=iterations, depth=6,
                             learning_rate=.05, l2_leaf_reg=5, random_seed=42,
                             thread_count=threads, allow_writing_files=False, verbose=False)
    started = time.monotonic()
    target = train["target"].to_numpy()
    if loss.startswith("Scaled"):
        target = target / np.maximum(train["sum84"].to_numpy()/3, 10)
    model.fit(train[FEATURES], target, cat_features=CAT_FEATURES)
    metadata = {"training_rows": len(train), "train_origin_min": str(train["origin"].min().date()),
                "train_origin_max": str(train["origin"].max().date()),
                "train_label_end_max": str(train["label_end"].max().date()),
                "cutoff": str(pd.Timestamp(cutoff).date()), "seconds": round(time.monotonic()-started, 3)}
    return model, metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sales", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--predictions", required=True, type=Path)
    parser.add_argument("--iterations", type=int, default=500)
    parser.add_argument("--threads", type=int, default=4)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    daily, audit = read_sales(args.sales)
    final_day = daily.columns[-1]
    evaluation_end = final_day - pd.Timedelta(days=1)
    test2 = evaluation_end-pd.Timedelta(days=HORIZON)
    test1 = test2-pd.Timedelta(days=HORIZON)
    validation = test1-pd.Timedelta(days=HORIZON)
    origins = pd.date_range(daily.columns[0]+pd.Timedelta(days=84), evaluation_end-pd.Timedelta(days=HORIZON), freq="7D")
    origins = sorted(set(origins) | {validation, test1, test2})
    frame = pd.concat([features_at(daily, date, True) for date in origins], ignore_index=True)
    print(json.dumps({"data": audit, "feature_rows": len(frame)}, ensure_ascii=False), flush=True)
    val = features_at(daily, validation, True)
    val_baselines = baseline_predictions(val)
    baseline_scores = {name: metrics(val["target"], prediction) for name, prediction in val_baselines.items()}
    baseline_name = min(baseline_scores, key=lambda n: baseline_scores[n]["wape"])
    candidates, candidate_predictions = {}, {}
    for loss in ("RMSE", "ScaledRMSE", "ScaledPoisson"):
        model, metadata = fit(frame, validation, loss, args.iterations, args.threads)
        prediction = predict_model(model, val, loss)
        candidates[loss] = {"fit": metadata, "metrics": metrics(val["target"], prediction)}
        candidate_predictions[loss] = prediction
        print(json.dumps({"validation_candidate": loss, **candidates[loss]}, ensure_ascii=False), flush=True)
    loss = min(candidates, key=lambda n: candidates[n]["metrics"]["wape"])
    preferred_method = "catboost" if candidates[loss]["metrics"]["wape"] < baseline_scores[baseline_name]["wape"] else baseline_name
    # These residuals are strictly from validation, never estimated from the two test windows.
    val_prediction = candidate_predictions[loss]
    scale = np.maximum(val["sum84"].to_numpy()/3, 1)
    residuals = (val["target"].to_numpy()-val_prediction)/scale
    residual_quantiles = np.quantile(residuals, [.1, .5, .9]).tolist()
    folds = []
    for cutoff in (test1, test2):
        current = features_at(daily, cutoff, True)
        model, metadata = fit(frame, cutoff, loss, args.iterations, args.threads)
        prediction = predict_model(model, current, loss)
        base = baseline_predictions(current)[baseline_name]
        scale = np.maximum(current["sum84"].to_numpy()/3, 1)
        lo = np.maximum(0, prediction+residual_quantiles[0]*scale)
        hi = np.maximum(0, prediction+residual_quantiles[2]*scale)
        future = daily.loc[:, cutoff+pd.Timedelta(days=1):cutoff+pd.Timedelta(days=HORIZON)].sum(axis=1)
        unknown = future.loc[~future.index.isin(current["sku"])]
        fold = {"origin": str(cutoff.date()), "forecast_end": str((cutoff+pd.Timedelta(days=HORIZON)).date()),
                "fit": metadata, "catboost": metrics(current["target"], prediction),
                "baseline_name": baseline_name, "baseline": metrics(current["target"], base),
                "interval_80_coverage": float(((current["target"] >= lo) & (current["target"] <= hi)).mean()),
                "not_yet_observed_skus_with_future_sales": int((unknown > 0).sum()),
                "not_yet_observed_future_quantity": float(unknown.sum())}
        folds.append(fold)
        print(json.dumps({"test": fold}, ensure_ascii=False), flush=True)
    final_model, final_fit = fit(frame, evaluation_end, loss, args.iterations, args.threads)
    model_path = args.output/"se-demand-28d.cbm"
    final_model.save_model(str(model_path))
    import hashlib
    model_sha256 = hashlib.sha256(model_path.read_bytes()).hexdigest()
    importances = sorted(zip(FEATURES, final_model.feature_importances_.tolist()), key=lambda row: -row[1])
    manifest = {"model_id": "se-demand-28d-"+model_sha256[:12], "model_sha256": model_sha256,
                "horizon_days": HORIZON, "candidate": loss,
                "loss_function": {"ScaledPoisson": "Poisson", "ScaledRMSE": "RMSE"}.get(loss, loss),
                "iterations": args.iterations, "depth": 6, "learning_rate": .05, "random_seed": 42,
                "features": FEATURES, "categorical_features": CAT_FEATURES,
                "target_transform": "target / max(sum84 / 3, 10)" if loss.startswith("Scaled") else "identity",
                "baseline_name": baseline_name, "preferred_method_on_validation": preferred_method,
                "residual_quantiles": residual_quantiles, "residual_scale": "max(sum84 / 3, 1)",
                "trained_as_of": str(evaluation_end.date()), "source_data_as_of": str(final_day.date()),
                "python": platform.python_version(), "catboost": catboost.__version__,
                "created_at": datetime.now(timezone.utc).isoformat(), "final_fit": final_fit}
    limitations = [
        "Target is observed positive invoice sales, not latent demand or net revenue.",
        "No client IDs: large one-off client orders cannot be reliably identified.",
        "No daily availability: stockout compensation is not trained or validated on real data.",
        "Large-day features are robust; actual targets are not secretly clipped or relabeled.",
        "Last observed day completeness is unverified; it is excluded from evaluation/training targets but used for current forecast features.",
        "Intervals are pooled validation-residual estimates; coverage is reported, not assumed to be calibrated per SKU.",
        "Supplier SE, one warehouse, units pieces; IEK and unseen products are outside this model's scope.",
        "Prices, stock, inbound, MOQ, individual order policy and LLM judgement belong to later modules described in the specification.",
        "No automatic retraining: infer reads fresh sales; train must be run explicitly."]
    limitations.append("Two test windows were inspected during development; final candidate selection uses validation only. A new untouched future window is needed before production acceptance.")
    report = {"source": audit, "validation_origin": str(validation.date()),
              "validation_forecast_end": str((validation+pd.Timedelta(days=HORIZON)).date()),
              "validation_baselines": baseline_scores, "validation_candidates": candidates,
              "selected_loss": loss, "selected_baseline": baseline_name, "preferred_method_on_validation": preferred_method,
              "test_folds": folds, "final_fit": final_fit, "feature_importance": importances,
              "test_aggregate": {key: aggregate_metrics(folds, key) for key in ("catboost", "baseline")},
              "limitations": limitations}
    (args.output/"manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    (args.output/"evaluation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    from infer import forecast
    prediction = forecast(args.sales, args.output)
    args.predictions.parent.mkdir(parents=True, exist_ok=True)
    prediction.to_csv(args.predictions, index=False, encoding="utf-8-sig")
    print(json.dumps({"saved_model": str(model_path), "model_bytes": model_path.stat().st_size,
                      "forecast_rows": len(prediction), "predictions": str(args.predictions)}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
