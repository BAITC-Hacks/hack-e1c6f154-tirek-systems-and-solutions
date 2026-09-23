"""Reconcile the full-fix benchmark independently and preserve earlier evidence."""
from decimal import Decimal, localcontext
import json
from pathlib import Path
import re

from .correction_evidence import evidence as prior_regressions
from .partner_io import csv_records, sha256
from .run import DEFAULT_ADAPTER, DEFAULT_CALCULATOR, verify_manifest

ROOT = Path(__file__).resolve().parent


def metrics(rows):
    if any(row["error"] or row["forecast_quantity"] is None for row in rows):
        raise AssertionError("Benchmark has missing/failed forecasts; do not score only successes")
    actual = sum((Decimal(str(r["realized_regular_quantity"])) for r in rows), Decimal(0))
    errors = [Decimal(str(r["forecast_quantity"])) - Decimal(str(r["realized_regular_quantity"])) for r in rows]
    absolute = sum(map(abs, errors), Decimal(0))
    return {"wape": float(absolute / actual) if actual else None,
            "mae": float(absolute / len(rows)) if rows else None,
            "bias": float(sum(errors, Decimal(0)) / actual) if actual else None}


def reconcile():
    result = {}
    for split in ("development", "final"):
        report = json.loads((ROOT / "reports" / ("full-" + split) / "report.json").read_text())
        old = json.loads((ROOT / "reports" / ("corrected-" + split) / "report.json").read_text())
        groups = [("overall", report["rows"], report["overall"])]
        for field in ("scenario", "seed", "origin", "sku", "unit"):
            for value, score in report["groups"][field].items():
                groups.append((field + "/" + value, [r for r in report["rows"] if str(r[field]) == value], score))
        for name, rows, score in groups:
            independent = metrics(rows)
            for short, long in (("wape", "wape_realized"), ("mae", "mae_realized"), ("bias", "bias_realized")):
                observed = independent[short]
                assert (observed is None and score[long] is None) or abs(observed - score[long]) < 1e-9, (split, name, short)
        key = lambda r: (r["seed"], r["scenario"], r["origin"], r["sku"])
        assert len({key(r) for r in report["rows"]}) == len(report["rows"])
        for target in ("realized_regular_quantity", "expected_regular_quantity", "project_quantity"):
            assert {key(r): r[target] for r in report["rows"]} == {key(r): r[target] for r in old["rows"]}, target
        saved = csv_records(ROOT / "reports" / ("full-" + split) / "rows.csv", ["forecast_quantity", "realized_regular_quantity"])
        assert len(saved) == len(report["rows"])
        for original, row in zip(report["rows"], saved):
            for field in ("forecast_quantity", "realized_regular_quantity", "expected_regular_quantity"):
                assert Decimal(row[field]) == Decimal(str(original[field]))
        result[split] = {"independent_decimal_metrics": metrics(report["rows"]), "groups_reconciled": len(groups),
                         "unchanged_targets": True, "csv_verified": True, "rows": len(saved),
                         "previous_wape": old["overall"]["wape_realized"], "new_wape": report["overall"]["wape_realized"],
                         "business_checks": {k: report["business"][k] for k in ("passed", "total", "integration_status")},
                         "paired_checks": {k: report["paired_project_checks"][k] for k in ("passed", "total")}}
    return result


def main():
    out = ROOT / "audits/full"
    manifest = verify_manifest(ROOT / "full-freeze.json", DEFAULT_ADAPTER, DEFAULT_CALCULATOR)
    tests = (out / "unit-tests.log").read_text()
    count = int(re.search(r"Ran (\d+) tests", tests).group(1))
    assert "\nOK\n" in tests
    with localcontext() as context:
        context.prec = 60
        comparison = reconcile()
        prior = prior_regressions()
    requirement = json.loads((out / "requirements.json").read_text())
    result = {"kind": "full_fix_verification_forecast_accuracy_separate_from_test_passes",
              "source_commit": manifest["source_commit"], "freeze_sha256": sha256(ROOT / "full-freeze.json"),
              "unit_tests": {"passed": count, "total": count},
              "must_have_controls": {"passed": requirement["passed"], "total": requirement["total"]},
              "prior_regressions": prior, "synthetic_comparison": comparison,
              "final_status": "Previously inspected final seeds; retrospective comparison, not new holdout",
              "real_metrics": "real-metrics.json independently checks saved CSVs against XLSX; it does not retrain",
              "real_replay": "real-replay.json records fresh inference from saved weights, not accuracy scoring",
              "unresolved_data_requirements": "real-review.json; blocked real orders are not successful procurement decisions"}
    (out / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"unit_tests": result["unit_tests"], "must_have_controls": result["must_have_controls"],
                      "synthetic_final": comparison["final"]["independent_decimal_metrics"]}))


if __name__ == "__main__":
    main()
