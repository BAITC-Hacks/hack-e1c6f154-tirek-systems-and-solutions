"""Run the saved real model in its own checkout and record reproducible provenance.

The external forecast-v2 checkout is read-only. No weights are refitted. The
original published dependency is commit 2282c2042dfb38a8aca17fb97219a55de28165ee.
Compatible later checkouts must retain the frozen feature/algorithm hashes.
"""
import argparse
import csv
from decimal import Decimal
import json
import os
from pathlib import Path
import subprocess

from .partner_io import csv_records, number, private_path, sha256
from .real_bridge import export


def compare_previous(path, previous):
    fields = ["supplier", "unit", "sku", "origin", "forecast", "selected_method"]
    current, before = (csv_records(p, fields) for p in (path, previous))
    def indexed(rows):
        result = {}
        for row in rows:
            key = tuple(row[f] for f in fields[:4])
            if key in result:
                raise ValueError("Duplicate forecast comparison identity")
            result[key] = row
        return result
    left, right = indexed(current), indexed(before)
    if set(left) != set(right) or any(left[key]["selected_method"] != right[key]["selected_method"] for key in left):
        raise ValueError("Saved forecast universe/method changed")
    differences = [abs(number(left[key]["forecast"]) - number(right[key]["forecast"])) for key in left]
    maximum = max(differences, default=Decimal(0))
    if maximum > Decimal("0.000001"):
        raise ValueError("Fresh saved-weight forecast differs from prior output beyond numeric tolerance")
    return {"rows": len(left), "previous_sha256": sha256(previous), "maximum_absolute_difference": str(maximum),
            "absolute_tolerance": "0.000001", "model_or_selection_refitted": False}


def replay(repo, python, inputs, private, report, previous=None):
    repo, inputs, report = (Path(p).resolve() for p in (repo, inputs, report))
    # Resolving a venv's python symlink would silently select its base runtime.
    python = Path(python).expanduser().absolute()
    private = private_path(private)
    private.mkdir(parents=True, exist_ok=True)
    model = repo / "model/forecast_v2"
    artifacts = model / "artifacts"
    selection = json.loads((artifacts / "selection.json").read_text())
    commit = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    dirty = subprocess.check_output(["git", "-C", str(repo), "status", "--porcelain", "--untracked-files=no", "--", "model/forecast_v2"], text=True)
    if dirty.strip():
        raise ValueError("External forecast model checkout must have no tracked modifications")
    recorded = selection.get("implementation_sha256", {})
    required = {"data.py", "features.py", "methods.py"}
    if not required.issubset(recorded):
        raise ValueError("Missing frozen algorithm/feature provenance")
    implementation = {name: recorded[name] for name in sorted(required)}
    for filename, expected in implementation.items():
        if sha256(model / filename) != expected:
            raise ValueError("External forecast implementation differs from frozen weights")
    forecasts = private_path(private / "forecast.csv")
    command = [str(python), "-m", "model.forecast_v2.run", "infer", "--inputs", str(inputs),
               "--output", str(artifacts), "--private", str(private), "--cache", str(private / "cache.pkl"),
               "--predictions", str(forecasts)]
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    log = private_path(private / "inference.log")
    with log.open("w") as handle:
        outcome = subprocess.run(command, cwd=repo, env=environment, stdout=handle, stderr=subprocess.STDOUT)
    if outcome.returncode:
        raise RuntimeError(f"Saved-model inference failed, exit {outcome.returncode}; see private inference.log")
    runtime = json.loads(subprocess.check_output([str(python), "-c",
        'import importlib.metadata,json,platform; print(json.dumps({"python":platform.python_version(),"packages":{k:importlib.metadata.version(k) for k in ["catboost","numpy","pandas","scipy","openpyxl"]}}))'], text=True))
    bridge = export(inputs, artifacts, forecasts, private, report.parent / "real-review.json")
    evidence = {"kind": "fresh_inference_from_saved_real_weights_not_retraining_or_backtest",
                "external_model_commit": commit, "published_compatible_base_commit": "2282c2042dfb38a8aca17fb97219a55de28165ee",
                "implementation_sha256": implementation, "runtime": runtime,
                "actual_inference_driver_sha256": sha256(model / "run.py"),
                "selection_sha256": sha256(artifacts / "selection.json"), "weights_manifest_sha256": sha256(artifacts / "manifest.json"),
                "forecast_sha256": sha256(forecasts), "private_log_sha256": sha256(log),
                "rows": bridge["rows"], "exit_code": outcome.returncode,
                "command": command, "working_directory": str(repo),
                "private_export_json_sha256": sha256(private / "procurement-review.json"),
                "private_export_csv_sha256": sha256(private / "procurement-review.csv"),
                "raw_partner_rows_in_this_report": False, "retrained": False,
                "independent_forecast_accuracy_evaluation": False}
    if previous:
        evidence["previous_forecast_comparison"] = compare_previous(forecasts, previous)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(evidence, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    return evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("repo", "python", "inputs", "private", "report"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--previous", type=Path)
    args = parser.parse_args()
    evidence = replay(args.repo, args.python, args.inputs, args.private, args.report, args.previous)
    print(json.dumps({"replayed_rows": evidence["rows"], "retrained": False, "exit_code": evidence["exit_code"]}))


if __name__ == "__main__":
    main()
