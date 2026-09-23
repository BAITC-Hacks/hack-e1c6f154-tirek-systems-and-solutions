"""Offline CLI: freeze candidate, evaluate development/final, export public inputs."""
import argparse
import csv
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import platform
import subprocess
import sys

from .adapters import call_external
from .business import check_cases
from .generator import PROTOCOL, generate
from .metrics import aggregate

ROOT = Path(__file__).resolve().parent
DEFAULT_ADAPTER = "model.testbed.baseline:forecast"
DEFAULT_CALCULATOR = "model.testbed.business:reference_calculate"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def file_hashes():
    files = sorted(ROOT.rglob("*.py")) + [ROOT / "protocol.json", ROOT / "fixtures/business_cases.json"]
    return {str(p.relative_to(ROOT)): digest(p.read_bytes()) for p in files}


def adapter_hash(spec):
    module = importlib.util.find_spec(spec.split(":", 1)[0])
    if module is None or not module.origin:
        raise ValueError(f"Adapter module not found: {spec}")
    return digest(Path(module.origin).read_bytes())


def dump(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")


def freeze(args):
    path = Path(args.manifest)
    if path.exists():
        raise ValueError("Freeze manifest already exists; do not overwrite a holdout candidate")
    manifest = {"version": PROTOCOL["version"], "protocol": PROTOCOL, "files": file_hashes(),
                "adapter": args.adapter, "adapter_sha256": adapter_hash(args.adapter),
                "calculator": args.calculator, "calculator_sha256": adapter_hash(args.calculator),
                "python_version": platform.python_version(),
                "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                "artifacts": {str(Path(p).resolve()): digest(Path(p).read_bytes()) for p in args.artifact}}
    dump(path, manifest)
    print(f"Frozen candidate: {path}; sha256={digest(path.read_bytes())}")


def verify_manifest(path, adapter, calculator):
    manifest = json.loads(Path(path).read_text())
    for field, actual in (("files", file_hashes()), ("protocol", PROTOCOL), ("adapter", adapter),
                          ("adapter_sha256", adapter_hash(adapter)), ("calculator", calculator),
                          ("calculator_sha256", adapter_hash(calculator)), ("python_version", platform.python_version())):
        if manifest[field] != actual:
            raise ValueError(f"Frozen candidate mismatch: {field}; final evaluation refused")
    for path, expected in manifest["artifacts"].items():
        if digest(Path(path).read_bytes()) != expected:
            raise ValueError(f"Frozen model artifact changed: {path}")
    return manifest


def forecast_checks(predictions, seeds):
    """MH4 is paired on the same regular path; acceptance thresholds predeclared."""
    rows = []
    for seed in seeds:
        for origin in PROTOCOL["origins"]:
            for index in range(PROTOCOL["items_per_case"]):
                sku = f"SKU-{index + 1:03d}"
                base = predictions[(seed, origin, "stable", sku)]
                for scenario in ("single_project", "split_project"):
                    changed = predictions[(seed, origin, scenario, sku)]
                    passed = False
                    forecast_difference = quantity_difference = None
                    if base is not None and changed is not None:
                        forecast_difference = abs(changed - base)
                        # Fixed procurement context: zero stock, no path, MOQ=multiple=1.
                        quantity_difference = abs(math.ceil(changed) - math.ceil(base))
                        passed = (forecast_difference <= PROTOCOL["project_forecast_tolerance_fraction"] * base + 1e-9
                                  and quantity_difference <= max(PROTOCOL["project_quantity_tolerance_fraction"] * math.ceil(base), PROTOCOL["project_minimum_batch"]))
                    rows.append({"id": f"MH4/{seed}/{origin}/{sku}/{scenario}", "passed": passed,
                                 "forecast_difference": forecast_difference, "quantity_difference": quantity_difference})
    return rows


def evaluate(args):
    manifest_hash = None
    if args.split == "final":
        verify_manifest(args.manifest, args.adapter, args.calculator)
        manifest_hash = digest(Path(args.manifest).read_bytes())
    seeds = PROTOCOL["development_seeds" if args.split == "development" else "final_seeds"]
    rows, predictions = [], {}
    for seed in seeds:
        for scenario in PROTOCOL["scenarios"]:
            for origin in PROTOCOL["origins"]:
                case = generate(scenario, seed, origin)
                error = None
                try:
                    forecast = call_external(args.adapter, case.observed, args.timeout)
                except Exception as exc:
                    forecast, error = {}, f"{type(exc).__name__}: {exc}"
                input_hash = digest(json.dumps(case.observed, sort_keys=True).encode())
                for item in case.observed["items"]:
                    sku = item["sku"]
                    realized = case.truth[sku]["regular_realized"][-PROTOCOL["horizon_days"]:]
                    expectation = case.truth[sku]["regular_expectation"][-PROTOCOL["horizon_days"]:]
                    project = case.projects[sku]["one_off_quantity"][-PROTOCOL["horizon_days"]:]
                    forecast_total = sum(forecast[sku]) if sku in forecast else None
                    predictions[(seed, origin, scenario, sku)] = forecast_total
                    rows.append({"seed": seed, "scenario": scenario, "origin": origin, "sku": sku, "unit": item["unit"],
                                 "forecast_quantity": forecast_total, "realized_regular_quantity": sum(realized),
                                 "expected_regular_quantity": sum(expectation), "project_quantity": sum(project),
                                 "daily_absolute_error": sum(abs(a-b) for a,b in zip(forecast[sku], realized)) if sku in forecast else None,
                                 "daily_absolute_error_expectation": sum(abs(a-b) for a,b in zip(forecast[sku], expectation)) if sku in forecast else None,
                                 "input_sha256": input_hash, "error": error})
            print(f"{args.split}: seed={seed}, scenario={scenario}", file=sys.stderr)
    business = check_cases(lambda request: call_external(args.calculator, request, args.timeout, kind="business"))
    overall = aggregate(rows)
    groups = {}
    for field in ("seed", "scenario", "origin", "unit"):
        groups[field] = {str(value): aggregate(r for r in rows if r[field] == value) for value in sorted({r[field] for r in rows})}
    groups["scenario_seed"] = {f"{scenario}/{seed}": aggregate(r for r in rows if r["scenario"] == scenario and r["seed"] == seed)
                               for scenario in PROTOCOL["scenarios"] for seed in seeds}
    checks = forecast_checks(predictions, seeds)
    report = {"schema_version": "testbed-report-v2", "mode": "synthetic", "split": args.split, "seeds": seeds,
              "adapter": args.adapter, "calculator": args.calculator, "python_version": platform.python_version(),
              "manifest_sha256": manifest_hash, "source_hashes": file_hashes(),
              "forecast_target_wape": PROTOCOL["forecast_target_wape"],
              "forecast_target_met": overall["wape_realized"] is not None and overall["wape_realized"] <= PROTOCOL["forecast_target_wape"],
              "overall": overall, "groups": groups, "rows": rows,
              "business": {"integration_status": "reference_only" if args.calculator == DEFAULT_CALCULATOR else "external_adapter",
                           "passed": sum(r["passed"] for r in business), "total": len(business), "cases": business},
              "paired_project_checks": {"passed": sum(r["passed"] for r in checks), "total": len(checks), "cases": checks},
              "interpretation": "Business pass rate is not forecast quality. Difficult cases are retained. WAPE target is not guaranteed.",
              "limitations": ["Synthetic data do not establish performance on partner data.",
                              "Expectation is conditional on private generator state, including unannounced shocks; it is not all knowable at cutoff.",
                              "Two origins are independently simulated ensembles, not a continuous rolling inventory simulation.",
                              "Trusted local adapter subprocess is a data boundary, not an OS sandbox.",
                              "Reference business results do not validate an unconnected production calculator.",
                              "Paired MH4 quantity uses a fixed zero-stock, unit-batch projection; full calculator cases are separate.",
                              "WAPE is pooled only for the common base unit шт; economics and other units are checked separately."]}
    output = Path(args.output)
    dump(output / "report.json", report)
    with (output / "rows.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (output / "report.md").write_text(markdown(report), encoding="utf-8")
    print(json.dumps({"output": str(output), "overall": overall, "business_passed": report["business"]["passed"],
                      "business_total": len(business), "forecast_target_met": report["forecast_target_met"]}, ensure_ascii=False))
    if overall["failed_rows"] or report["business"]["passed"] != len(business) or report["paired_project_checks"]["passed"] != len(checks):
        return 2
    if args.require_target and not report["forecast_target_met"]:
        return 3
    return 0


def percent(value):
    return "undefined" if value is None else f"{100 * value:.2f}%"


def markdown(report):
    overall, business = report["overall"], report["business"]
    lines = [f"# Testbed v2 — {report['split']}", "", f"Synthetic; adapter `{report['adapter']}`; seeds {report['seeds']}.", "",
             f"WAPE по реализованному скрытому регулярному спросу: **{percent(overall['wape_realized'])}**.",
             f"WAPE относительно математического ожидания: **{percent(overall['wape_expectation'])}**.",
             f"Цель ≤10%: **{'достигнута' if report['forecast_target_met'] else 'не достигнута'}**; это не гарантия качества.",
             f"Бизнес-проверки: **{business['passed']}/{business['total']}**, статус `{business['integration_status']}`.",
             f"Парные MH4 (прогноз и количество): **{report['paired_project_checks']['passed']}/{report['paired_project_checks']['total']}**.",
             f"Строк: {overall['rows']}; ошибок адаптера: {overall['failed_rows']}. Проектный спрос исключён из цели, сохранён отдельно.",
             "90% пройденных тестов не означает 90% качества прогноза.", "",
             "| Сценарий / seed | Строк | WAPE реализация | WAPE ожидание | Ошибки |", "|---|---:|---:|---:|---:|"]
    for key, group in report["groups"]["scenario_seed"].items():
        lines.append(f"| {key} | {group['rows']} | {percent(group['wape_realized'])} | {percent(group['wape_expectation'])} | {group['failed_rows']} |")
    lines += ["", "| Seed | WAPE реализация | WAPE ожидание |", "|---|---:|---:|"]
    for key, group in report["groups"]["seed"].items():
        lines.append(f"| {key} | {percent(group['wape_realized'])} | {percent(group['wape_expectation'])} |")
    lines += ["", "| Бизнес-проверка | Правила | Результат |", "|---|---|---|"]
    for case in business["cases"]:
        lines.append(f"| {case['id']} | {', '.join(case['requirements'])} | {'PASS' if case['passed'] else 'FAIL: ' + '; '.join(case['failures'])} |")
    lines += ["", "## Ограничения", ""] + [f"- {value}" for value in report["limitations"]]
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    frozen = commands.add_parser("freeze")
    frozen.add_argument("--artifact", action="append", default=[], help="Model weights/config dependency file to freeze (repeatable)")
    run = commands.add_parser("evaluate")
    run.add_argument("--split", choices=("development", "final"), default="development")
    run.add_argument("--output", required=True)
    run.add_argument("--timeout", type=float, default=30)
    run.add_argument("--require-target", action="store_true")
    for command in (frozen, run):
        command.add_argument("--adapter", default=DEFAULT_ADAPTER)
        command.add_argument("--calculator", default=DEFAULT_CALCULATOR)
        command.add_argument("--manifest", default=str(ROOT / "freeze.json"))
    export = commands.add_parser("export-development")
    export.add_argument("--scenario", choices=PROTOCOL["scenarios"], default="stable")
    export.add_argument("--seed", type=int, choices=PROTOCOL["development_seeds"], default=101)
    export.add_argument("--output", required=True)
    business = commands.add_parser("check-business")
    business.add_argument("--calculator", default=DEFAULT_CALCULATOR)
    business.add_argument("--timeout", type=float, default=30)
    business.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        if args.command == "freeze":
            freeze(args)
        elif args.command == "evaluate":
            return evaluate(args)
        elif args.command == "check-business":
            rows = check_cases(lambda request: call_external(args.calculator, request, args.timeout, kind="business"))
            result = {"calculator": args.calculator, "integration_status": "reference_only" if args.calculator == DEFAULT_CALCULATOR else "external_adapter",
                      "passed": sum(r["passed"] for r in rows), "total": len(rows), "cases": rows}
            dump(args.output, result)
            print(f"Business checks: {result['passed']}/{result['total']}; {result['integration_status']}")
            return 0 if result["passed"] == len(rows) else 2
        else:
            case = generate(args.scenario, args.seed, PROTOCOL["origins"][0])
            dump(Path(args.output) / "model-input.json", case.observed)
            dump(Path(args.output) / "evaluator-only/regular-truth.json", case.truth)
            dump(Path(args.output) / "evaluator-only/project-purchases.json", case.projects)
    except (ValueError, OSError) as exc:
        parser.exit(2, f"{exc}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
