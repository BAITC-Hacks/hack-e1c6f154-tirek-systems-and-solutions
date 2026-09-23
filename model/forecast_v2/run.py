"""CLI: select on three validation origins, freeze, evaluate, save fitted weights."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import pickle
import time
import numpy as np
import pandas as pd
from .data import load_panels, sha256
from .features import features_at, scoring_frame
from .methods import (MODEL_SPECS, adaptive_predictions, fit_model, predict_model,
                      simple_predictions, save_model, load_model)

HERE = Path(__file__).resolve().parent
PROTOCOL = json.loads((HERE / "protocol.json").read_text())
VALIDATION = PROTOCOL["validation_origins"]
EVALUATION = PROTOCOL["evaluation_origins"]
PREDICTION_IMPLEMENTATION_FILES = ("data.py", "features.py", "methods.py")


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def log(**value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


def prediction_implementation_hashes():
    """Hashes of code that defines features, fitted values and transforms."""
    return {name: sha256(HERE / name) for name in PREDICTION_IMPLEMENTATION_FILES}


def verify_prediction_implementation(frozen):
    recorded = frozen.get("implementation_sha256", {})
    actual = prediction_implementation_hashes()
    mismatches = [name for name, value in actual.items() if recorded.get(name) != value]
    if mismatches:
        raise ValueError("Prediction implementation differs from frozen selection: " + ", ".join(mismatches))


def metrics(actual, predicted):
    actual, predicted = np.asarray(actual, float), np.asarray(predicted, float)
    if len(actual) != len(predicted) or not np.isfinite(predicted).all() or (predicted < 0).any():
        raise ValueError("Invalid forecast vector")
    total, error = float(actual.sum()), float(np.abs(predicted - actual).sum())
    bias = float((predicted - actual).sum())
    return {"sku_windows": len(actual), "actual_quantity": total, "predicted_quantity": float(predicted.sum()),
            "absolute_error": error, "mae": error / len(actual) if len(actual) else None,
            "wape": error / total if total else None, "bias": bias / total if total else None,
            "bias_units": bias, "zero_actual_rows": int((actual == 0).sum()),
            "absolute_error_on_zero_actual": float(predicted[actual == 0].sum())}


def summarize(table, method):
    result = {"overall": metrics(table.target, table[method]), "by_group": {}, "by_abc": {}, "by_origin": {}}
    for field, key in (("group", "by_group"), ("abc", "by_abc"), ("origin", "by_origin")):
        for name, group in table.groupby(field):
            result[key][str(name)] = metrics(group.target, group[method])
    return result


def score_rows(panel, frame, predictions):
    result = frame[["sku", "origin", "target", "group", "abc"]].reset_index(drop=True).copy()
    for name, values in predictions.items():
        result[name] = np.asarray(values)
    unseen = scoring_frame(panel, frame, frame.origin.iloc[0])
    if len(unseen):
        extra = pd.DataFrame({"sku": unseen.index, "origin": frame.origin.iloc[0], "target": unseen.values,
                              "group": "unseen", "abc": "unseen"})
        for name in predictions:
            extra[name] = 0.
        result = pd.concat([result, extra], ignore_index=True)
    result["origin"] = pd.to_datetime(result.origin).dt.strftime("%Y-%m-%d")
    return result


def history(panel, use2024=True):
    end = panel.daily.columns[-1] - pd.Timedelta(days=29)
    origins = pd.date_range(panel.daily.columns[0] + pd.Timedelta(days=167), end, freq="7D")
    origins = sorted(set(origins) | {pd.Timestamp(x) for x in VALIDATION + EVALUATION if pd.Timestamp(x) <= end})
    frames, simple = [], []
    for origin in origins:
        f = features_at(panel, origin, target=True, history2024=use2024)
        frames.append(f)
        simple.append(simple_predictions(panel, f))
    return pd.concat(frames, ignore_index=True), pd.concat(simple, ignore_index=True)


def load_state(args):
    cache = args.cache
    source_hashes = {str(p.relative_to(args.inputs)): sha256(p) for p in args.inputs.rglob("*.xlsx")
                     if p.name.startswith(("Динамика", "Ежемесячные продажи"))}
    code_hash = {p.name: sha256(p) for p in HERE.glob("*.py") if not p.name.startswith("test_")}
    key = {"sources": source_hashes, "code": code_hash, "suppliers": args.suppliers}
    if cache.exists():
        with cache.open("rb") as stream:
            state = pickle.load(stream)
        if state["key"] == key:
            return state
    panels, audits = load_panels(args.inputs, tuple(args.suppliers))
    state = {"key": key, "panels": panels, "audits": audits, "history": {}}
    for panel in panels:
        log(build_history=panel.name, skus=len(panel.daily))
        state["history"][panel.name] = history(panel)
    cache.parent.mkdir(parents=True, exist_ok=True)
    with cache.open("wb") as stream:
        pickle.dump(state, stream)
    return state


def select(args, state):
    output = args.output
    frozen = {"protocol": PROTOCOL, "protocol_sha256": sha256(HERE / "protocol.json"),
              "iterations": args.iterations, "threads": args.threads, "source_hashes": state["key"]["sources"],
              "panels": {}, "frozen_at_utc": datetime.now(timezone.utc).isoformat()}
    report = {"sources": state["audits"], "panels": {}}
    for panel in state["panels"]:
        hist, hist_simple = state["history"][panel.name]
        tables, fits = [], []
        for origin in VALIDATION:
            current = features_at(panel, origin, target=True)
            simple = simple_predictions(panel, current)
            predictions = simple.to_dict("list")
            predictions.update(adaptive_predictions(current, simple, hist, hist_simple))
            for name in MODEL_SPECS:
                started = time.monotonic()
                model, fit = fit_model(name, hist, origin, args.iterations, args.threads)
                prediction = predict_model(name, model, current)
                predictions[name] = prediction
                if name.startswith("cb_"):
                    for weight in (.25, .5, .75):
                        predictions[f"blend:{name}:{weight}"] = weight * prediction + (1-weight) * simple.v1_baseline.to_numpy()
                fits.append(fit)
                score = metrics(current.target, prediction)
                log(panel=panel.name, validation=origin, candidate=name,
                    wape=None if score["wape"] is None else round(score["wape"], 5),
                    seconds=round(time.monotonic()-started, 2))
            tables.append(score_rows(panel, current, predictions))
        table = pd.concat(tables, ignore_index=True)
        methods = list(predictions)
        ranking = {name: summarize(table, name) for name in methods}
        selected = min(methods, key=lambda n: ranking[n]["overall"]["absolute_error"])
        best_ml = min(MODEL_SPECS, key=lambda n: ranking[n]["overall"]["absolute_error"])
        best_simple = min(hist_simple.columns, key=lambda n: ranking[n]["overall"]["absolute_error"])
        best_adaptive = min((n for n in methods if n.startswith("adaptive_")), key=lambda n: ranking[n]["overall"]["absolute_error"])
        group_policy, routed = {}, np.zeros(len(table))
        for group, subset in table.groupby("group"):
            name = min(methods, key=lambda n: np.abs(subset[n] - subset.target).sum())
            group_policy[group] = name
            routed[subset.index] = subset[name]
        table["static_group_apparent"] = routed
        frozen["panels"][panel.name] = {"selected": selected, "best_ml": best_ml, "best_simple": best_simple,
                                      "best_adaptive": best_adaptive, "static_group_diagnostic": group_policy,
                                      "unit": panel.unit, "supplier": panel.supplier}
        report["panels"][panel.name] = {"selected": selected, "candidates": ranking, "fits": fits,
            "static_group_diagnostic": {"apparent": summarize(table, "static_group_apparent"),
                "note": "Group mapping fitted on validation: apparent score is a selection-sample diagnostic, not prospective accuracy."}}
        table.to_csv(args.private / f"{panel.name}-validation.csv", index=False)
        log(selected=selected, panel=panel.name, metrics=ranking[selected]["overall"])
    write_json(output / "selection.json", frozen)
    write_json(output / "validation.json", report)
    write_json(output / "selection.lock.json", {"selection_sha256": sha256(output / "selection.json")})
    return frozen


def read_selection(output, state):
    path = output / "selection.json"
    lock = json.loads((output / "selection.lock.json").read_text())
    if sha256(path) != lock["selection_sha256"]:
        raise ValueError("Selection changed after freeze")
    frozen = json.loads(path.read_text())
    if frozen["source_hashes"] != state["key"]["sources"]:
        raise ValueError("Evaluation sources differ from frozen selection")
    if frozen["protocol_sha256"] != sha256(HERE / "protocol.json"):
        raise ValueError("Protocol changed after freeze")
    verify_prediction_implementation(frozen)
    return frozen


def forecast_methods(panel, frame, names, hist, hist_simple, iterations, threads, weights=None):
    simple = simple_predictions(panel, frame)
    result, fits, trained = {}, [], {}
    adaptive = None
    expanded = set(names)
    for name in names:
        if name.startswith("mix|"):
            _, left, right, _ = name.split("|")
            expanded.update([left, right])
    for name in sorted(expanded):
        if name.startswith("mix|"):
            continue
        if name in simple:
            result[name] = simple[name].to_numpy()
        elif name.startswith("adaptive_"):
            if adaptive is None:
                adaptive = adaptive_predictions(frame, simple, hist, hist_simple)
            result[name] = adaptive[name]
        else:
            model_name = name.split(":")[1] if name.startswith("blend:") else name
            if model_name not in trained:
                if weights is None:
                    model, fit = fit_model(model_name, hist, frame.origin.iloc[0], iterations, threads)
                    fits.append(fit)
                else:
                    model = weights[model_name]
                trained[model_name] = model
            pred = predict_model(model_name, trained[model_name], frame)
            if name.startswith("blend:"):
                weight = float(name.split(":")[2])
                pred = weight * pred + (1-weight) * simple.v1_baseline.to_numpy()
            result[name] = pred
    for name in names:
        if name.startswith("mix|"):
            _, left, right, weight = name.split("|")
            result[name] = float(weight)*result[left] + (1-float(weight))*result[right]
    return {name: result[name] for name in names}, fits


def evaluate(args, state, frozen):
    report = {"evaluation_status": PROTOCOL["evaluation_status"],
              "selection_sha256": sha256(args.output / "selection.json"), "panels": {}, "by_unit": {}}
    unit_tables = {}
    for panel in state["panels"]:
        policy = frozen["panels"][panel.name]
        hist, hist_simple = state["history"][panel.name]
        names = sorted(set([policy[k] for k in ("selected", "best_ml", "best_simple", "best_adaptive")] + ["v1_baseline"]))
        names = sorted(set(names + list(policy["static_group_diagnostic"].values())))
        tables, fits = [], []
        for origin in EVALUATION:
            current = features_at(panel, origin, target=True)
            pred, fit = forecast_methods(panel, current, names, hist, hist_simple, frozen["iterations"], frozen["threads"])
            fits.extend(fit)
            pred["selected"] = pred[policy["selected"]]
            pred["static_group_diagnostic"] = np.array([pred[policy["static_group_diagnostic"].get(g, policy["selected"])][i]
                                                        for i, g in enumerate(current.group)])
            table = score_rows(panel, current, pred)
            tables.append(table)
            log(panel=panel.name, retrospective_evaluation=origin, selected=policy["selected"], metrics=metrics(table.target, table.selected))
        table = pd.concat(tables, ignore_index=True)
        table.to_csv(args.private / f"{panel.name}-evaluation.csv", index=False)
        report["panels"][panel.name] = {"selected": policy["selected"], "unit": panel.unit,
            "methods": {name: summarize(table, name) for name in pred}, "fits": fits}
        diagnostics = {}
        for origin, subset in table.groupby("origin"):
            errors = np.abs(subset.selected - subset.target).sort_values(ascending=False)
            diagnostics[origin] = {"top10_share_absolute_error": float(errors.head(10).sum()/max(errors.sum(), 1)),
                "top10_share_volume": float(subset.target.nlargest(10).sum()/max(subset.target.sum(), 1)),
                "target_total": float(subset.target.sum()),
                "unseen_future_skus": int(subset.group.eq("unseen").sum()),
                "unseen_future_quantity": float(subset.loc[subset.group.eq("unseen"), "target"].sum())}
        report["panels"][panel.name]["diagnostics"] = diagnostics
        unit_tables.setdefault(panel.unit, []).append(table)
    for unit, tables in unit_tables.items():
        report["by_unit"][unit] = summarize(pd.concat(tables, ignore_index=True), "selected")
    scores = [v["methods"]["selected"]["overall"]["wape"] for v in report["panels"].values()]
    report["threshold_achieved_all_panels"] = bool(scores) and all(value is not None and value <= .1 for value in scores)
    write_json(args.output / "evaluation.json", report)
    return report


def fit_final(args, state, frozen):
    manifest = {"selection_sha256": sha256(args.output / "selection.json"), "python": platform.python_version(),
                "panels": {}, "model_specs": MODEL_SPECS}
    for panel in state["panels"]:
        policy = frozen["panels"][panel.name]
        hist, _ = state["history"][panel.name]
        cutoff = panel.daily.columns[-1] - pd.Timedelta(days=1)
        names = {policy["best_ml"]}
        selected = policy["selected"]
        if selected.startswith("mix|"):
            names.update(n for n in selected.split("|")[1:3] if n in MODEL_SPECS)
        if selected in MODEL_SPECS:
            names.add(selected)
        elif selected.startswith("blend:"):
            names.add(selected.split(":")[1])
        entry = {"selected": selected, "best_ml": policy["best_ml"], "trained_as_of": str(cutoff.date()),
                 "last_unverified_source_day_excluded": str(panel.daily.columns[-1].date()), "models": {}}
        for name in sorted(names):
            model, fit = fit_model(name, hist, cutoff, frozen["iterations"], frozen["threads"])
            path = save_model(name, model, args.output / f"{panel.name}-{name}")
            entry["models"][name] = {"file": path.name, "sha256": sha256(path), "fit": fit}
            log(saved_weights=path.name, panel=panel.name)
        manifest["panels"][panel.name] = entry
    write_json(args.output / "manifest.json", manifest)
    return manifest


def infer(args, state, frozen):
    verify_prediction_implementation(frozen)
    manifest = json.loads((args.output / "manifest.json").read_text())
    if manifest["selection_sha256"] != sha256(args.output / "selection.json"):
        raise ValueError("Model and selection mismatch")
    if manifest.get("model_specs") != MODEL_SPECS:
        raise ValueError("Saved model specifications differ from current implementation")
    results = []
    for panel in state["panels"]:
        entry = manifest["panels"][panel.name]
        origin = pd.Timestamp(args.origin) if args.origin else panel.daily.columns[-1] - pd.Timedelta(days=1)
        if origin < pd.Timestamp(entry["trained_as_of"]):
            raise ValueError("Final weights cannot be used to backcast before training cutoff")
        current = features_at(panel, origin)
        hist, hist_simple = state["history"][panel.name]
        weights = {}
        for name, meta in entry["models"].items():
            path = args.output / meta["file"]
            if sha256(path) != meta["sha256"]:
                raise ValueError("Model SHA-256 mismatch")
            weights[name] = load_model(name, path)
        predictions, _ = forecast_methods(panel, current, [entry["selected"], entry["best_ml"], "v1_baseline"],
                                          hist, hist_simple, frozen["iterations"], frozen["threads"], weights)
        result = current[["sku", "origin", "group", "abc"]].copy()
        result["supplier"] = panel.supplier
        result["unit"] = panel.unit
        result["warehouse"] = panel.warehouse
        result["forecast_start"] = origin + pd.Timedelta(days=1)
        result["forecast_end"] = origin + pd.Timedelta(days=28)
        result["selected_method"] = entry["selected"]
        result["forecast"] = predictions[entry["selected"]]
        result["best_ml_forecast"] = predictions[entry["best_ml"]]
        result["v1_baseline"] = predictions["v1_baseline"]
        results.append(result)
    path = args.predictions or args.private / "forecast.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.concat(results, ignore_index=True).to_csv(path, index=False)
    log(predictions=str(path), rows=sum(len(x) for x in results))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["all", "select", "evaluate", "fit", "infer"])
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=HERE / "artifacts")
    parser.add_argument("--private", type=Path, required=True, help="Directory outside Git for row-level predictions")
    parser.add_argument("--cache", type=Path, required=True, help="Trusted local pickle cache outside Git")
    parser.add_argument("--suppliers", nargs="+", choices=["SE", "IEK"], default=["SE", "IEK"])
    parser.add_argument("--iterations", type=int, default=500)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--origin", help="Inference origin; must not predate saved weights")
    parser.add_argument("--predictions", type=Path)
    args = parser.parse_args()
    repo_root = HERE.parents[1]
    for private_path in (args.private, args.cache, args.predictions):
        if private_path is not None and private_path.resolve().is_relative_to(repo_root):
            parser.error("Private caches and row-level predictions must be outside the Git worktree")
    if args.iterations <= 0 or args.threads <= 0:
        parser.error("iterations and threads must be positive")
    if args.command in ("select", "all") and (args.output / "evaluation.json").exists():
        parser.error("Use a fresh output directory: this run has already been evaluated")
    args.output.mkdir(parents=True, exist_ok=True)
    args.private.mkdir(parents=True, exist_ok=True)
    state = load_state(args)
    if args.command in ("all", "select"):
        frozen = select(args, state)
        if args.command == "all":
            from .refine import refine
            frozen = refine(args.output, args.private)
    else:
        if args.command == "infer":
            frozen = json.loads((args.output / "selection.json").read_text())
        else:
            frozen = read_selection(args.output, state)
    if args.command in ("all", "evaluate"):
        evaluate(args, state, frozen)
    if args.command in ("all", "fit"):
        fit_final(args, state, frozen)
    if args.command in ("all", "infer"):
        infer(args, state, frozen)


if __name__ == "__main__":
    main()
