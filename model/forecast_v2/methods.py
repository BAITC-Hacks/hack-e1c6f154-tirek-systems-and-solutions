"""Seasonal, intermittent, regression and boosting candidates; no holdout tuning."""
from pathlib import Path
import json
import numpy as np
import pandas as pd
from catboost import CatBoostRegressor


def simple_predictions(panel, frame):
    origin = frame.origin.iloc[0]
    h = panel.daily.loc[frame.sku, :origin].to_numpy(float)
    p = {"zero": np.zeros(len(frame))}
    for size in (28, 56, 84, 168, 364):
        p[f"mean{size}"] = frame[f"rate{size}"].to_numpy()
    for size in (3, 6):
        blocks = h[:, -28*size:].reshape(len(h), size, 28).sum(axis=2)
        p[f"median{size}"] = np.median(blocks, axis=1)
    year = frame.year28.fillna(frame.rate56).to_numpy()
    p["year28"] = year
    p["year56"] = frame.year56.fillna(frame.rate56).to_numpy()
    for alpha in (.25, .5, .75):
        for size in (56, 84, 168):
            p[f"yearblend_{alpha}_{size}"] = alpha * year + (1-alpha) * p[f"mean{size}"]
    p["v1_baseline"] = p["yearblend_0.5_56"].copy()
    for strength in (.25, .5, 1.):
        p[f"yeartrend_{strength}"] = year * (1 + strength * (frame.year_trend.to_numpy() - 1))
    for alpha in (0., .5, 1.):
        monthly = frame.monthly_year28.fillna(frame.rate84).to_numpy()
        p[f"monthlyblend_{alpha}"] = alpha * monthly + (1-alpha) * frame.monthly_season_rate.to_numpy()
    for alpha in (.1, .3, .6):
        length = h.shape[1] // 7 * 7
        weeks = h[:, -length:].reshape(len(h), -1, 7).sum(axis=2)
        level = weeks[:, 0].copy()
        for week in weeks[:, 1:].T:
            level = (1-alpha) * level + alpha * week
        p[f"ses_weekly_{alpha}"] = 4 * level
    # Croston-SBA and TSB at weekly frequency.
    for alpha in (.05, .15, .3):
        q = np.zeros(len(h)); interval = np.ones(len(h)); gap = np.ones(len(h))
        prob = np.zeros(len(h)); started = np.zeros(len(h), dtype=bool)
        for i, week in enumerate(weeks.T):
            positive = week > 0
            first = positive & ~started
            repeat = positive & started
            q[first] = week[first]; interval[first] = i + 1
            q[repeat] = (1-alpha) * q[repeat] + alpha * week[repeat]
            interval[repeat] = (1-alpha) * interval[repeat] + alpha * gap[repeat]
            prob = (1-alpha) * prob + alpha * positive
            prob[first] = 1 / (i + 1)
            gap = np.where(positive, 1, gap + 1)
            started |= positive
        p[f"sba_{alpha}"] = 4 * (1-alpha/2) * q / np.maximum(interval, 1)
        p[f"tsb_{alpha}"] = 4 * q * prob
    return pd.DataFrame(p, index=frame.index).clip(lower=0)


MODEL_SPECS = {
    "ridge_1": {"kind": "ridge", "alpha": 1.},
    "ridge_100": {"kind": "ridge", "alpha": 100.},
    "cb_mae_d4": {"kind": "cb", "loss": "MAE", "depth": 4, "scaled": False, "sku": True},
    "cb_mae_d6": {"kind": "cb", "loss": "MAE", "depth": 6, "scaled": False, "sku": True},
    "cb_scaled_mae_d4": {"kind": "cb", "loss": "MAE", "depth": 4, "scaled": True, "sku": True},
    "cb_scaled_mae_d6": {"kind": "cb", "loss": "MAE", "depth": 6, "scaled": True, "sku": True},
    "cb_scaled_mae_nosku": {"kind": "cb", "loss": "MAE", "depth": 5, "scaled": True, "sku": False},
    "cb_scaled_poisson": {"kind": "cb", "loss": "Poisson", "depth": 6, "scaled": True, "sku": True},
    "cb_rmse_d5": {"kind": "cb", "loss": "RMSE", "depth": 5, "scaled": False, "sku": True},
}


def model_matrix(frame, with_sku=True):
    excluded = {"origin", "label_end", "target", "group", "abc", "sku"}
    numeric = frame.drop(columns=[c for c in excluded if c in frame]).copy()
    fields = [c for c in numeric if c.startswith(("rate", "year", "block", "max", "std", "monthly_"))
              and c not in {"year_trend", "monthly_season_ratio"}]
    for name in fields:
        numeric[name + "_relative"] = numeric[name] / frame.scale
    numeric = numeric.replace([np.inf, -np.inf], np.nan).fillna(-1.)
    numeric["group"] = frame.group
    numeric["abc"] = frame.abc
    if with_sku:
        numeric["sku"] = frame.sku
    return numeric


def ridge_matrix(frame):
    fields = ["rate28", "rate56", "rate84", "rate168", "rate364", "year28", "year56", "year84", "monthly_year28", "monthly_2year28", "monthly_season_rate"]
    x = frame[fields].div(frame.scale, axis=0).fillna(0).clip(-10, 50).to_numpy()
    return np.column_stack([np.ones(len(x)), x, frame.month_sin, frame.month_cos,
                            frame.days_since_sale.to_numpy()/168])


def fit_model(name, history, cutoff, iterations=500, threads=4):
    spec = MODEL_SPECS[name]
    train = history.loc[history.label_end <= pd.Timestamp(cutoff)].copy()
    if train.empty:
        raise ValueError("No completed training labels")
    meta = {"rows": len(train), "origin_min": str(train.origin.min().date()),
            "origin_max": str(train.origin.max().date()), "label_end_max": str(train.label_end.max().date()),
            "cutoff": str(pd.Timestamp(cutoff).date()), "name": name, "spec": spec}
    y = train.target.to_numpy(float)
    if spec["kind"] == "ridge":
        x = ridge_matrix(train)
        y = y / train.scale.to_numpy()
        weights = np.sqrt(train.scale.to_numpy())
        penalty = np.eye(x.shape[1]) * spec["alpha"]
        penalty[0, 0] = 0
        coef = np.linalg.solve(x.T @ (x * weights[:, None]) + penalty, x.T @ (y * weights))
        model = {"coef": coef.tolist()}
    else:
        x = model_matrix(train, spec["sku"])
        cat = [c for c in ("sku", "group", "abc") if c in x]
        weights = None
        if spec["scaled"]:
            y = y / train.scale.to_numpy()
            if spec["loss"] == "MAE":
                weights = train.scale.to_numpy() / train.scale.mean()
        model = CatBoostRegressor(loss_function=spec["loss"], iterations=iterations, depth=spec["depth"],
                                  learning_rate=.04, l2_leaf_reg=10, random_seed=42, thread_count=threads,
                                  verbose=False, allow_writing_files=False)
        model.fit(x, y, cat_features=cat, sample_weight=weights)
    return model, meta


def predict_model(name, model, frame):
    spec = MODEL_SPECS[name]
    if spec["kind"] == "ridge":
        pred = (ridge_matrix(frame) @ np.asarray(model["coef"])) * frame.scale.to_numpy()
    else:
        kind = "Exponent" if spec["loss"] == "Poisson" else "RawFormulaVal"
        pred = model.predict(model_matrix(frame, spec["sku"]), prediction_type=kind)
        if spec["scaled"]:
            pred *= frame.scale.to_numpy()
    if not np.isfinite(pred).all():
        raise ValueError("Non-finite forecast")
    return np.maximum(0, pred)


def save_model(name, model, path):
    path = Path(path)
    if MODEL_SPECS[name]["kind"] == "ridge":
        path = path.with_suffix(".json")
        path.write_text(json.dumps(model, indent=2) + "\n")
    else:
        path = path.with_suffix(".cbm")
        model.save_model(str(path))
    return path


def load_model(name, path):
    if MODEL_SPECS[name]["kind"] == "ridge":
        return json.loads(Path(path).read_text())
    model = CatBoostRegressor()
    model.load_model(str(path))
    return model


def adaptive_predictions(frame, simple, past_frames, past_predictions):
    """Choose using completed rolling errors only, shrinking SKU scores to group scores."""
    origin = frame.origin.iloc[0]
    take = past_frames.label_end.le(origin) & past_frames.origin.ge(origin - pd.Timedelta(days=364))
    past = past_frames.loc[take].copy()
    preds = past_predictions.loc[take, simple.columns]
    if past.empty:
        return {"adaptive_group": simple.v1_baseline.to_numpy(), **{f"adaptive_sku_{k}": simple.v1_baseline.to_numpy() for k in (0, 3, 10)}}
    ages = (origin - past.label_end).dt.days.to_numpy()
    weights = 2 ** (-ages / 180)
    errors = np.abs(preds.to_numpy() - past.target.to_numpy()[:, None])
    errors_norm = errors / past.scale.to_numpy()[:, None]
    group_scores = {}
    global_score = (errors * weights[:, None]).sum(axis=0) / (past.scale.to_numpy() * weights).sum()
    for group in frame.group.unique():
        mask = past.group.eq(group).to_numpy()
        group_scores[group] = ((errors[mask] * weights[mask, None]).sum(axis=0) /
                               max((past.scale.to_numpy()[mask] * weights[mask]).sum(), 1)) if mask.any() else global_score
    group_rows = np.stack([group_scores[g] for g in frame.group])
    result = {"adaptive_group": simple.to_numpy()[np.arange(len(frame)), group_rows.argmin(axis=1)]}
    weighted = pd.DataFrame(errors_norm * weights[:, None], index=past.sku, columns=simple.columns).groupby(level=0).sum().reindex(frame.sku, fill_value=0).to_numpy()
    counts = pd.Series(weights, index=past.sku).groupby(level=0).sum().reindex(frame.sku, fill_value=0).to_numpy()
    for k in (0, 3, 10):
        scores = (weighted + k * group_rows) / np.maximum(counts[:, None] + k, 1e-9)
        scores[counts == 0] = group_rows[counts == 0]
        result[f"adaptive_sku_{k}"] = simple.to_numpy()[np.arange(len(frame)), scores.argmin(axis=1)]
    return result
