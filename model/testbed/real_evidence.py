"""Independently verify saved real predictions against original observed XLSX rows.

No fitting, model imports, latent correction, monthly duplication or SKU export.
Uses Decimal and a separately implemented event-time/group/metric calculation.
The July/August windows were inspected before: this is retrospective verification.
"""
import argparse
from collections import Counter, defaultdict
import csv
from datetime import date, datetime, timedelta
from decimal import Decimal, localcontext
import json
from pathlib import Path

from .partner_io import SUPPLIERS, UNITS, number, one_file, sha256, workbook_rows, csv_records

ZERO = Decimal(0)
START = date(2025, 1, 1)
ORIGINS = {"validation": ["2026-05-04", "2026-06-01", "2026-06-29"],
           "evaluation": ["2026-07-27", "2026-08-24"]}
TARGET_TOLERANCE = Decimal("0.000001")


def observed_sales(path):
    daily = defaultdict(lambda: defaultdict(Decimal))
    units, warehouses = {}, set()
    counts = Counter()
    seen_header = False
    for row, values in workbook_rows(path):
        if row == 1:
            if [values.get(c) for c in "ABCDEFGH"] != ["Дата", "Номер", "Документ", "Код", "Номенклатура", "Ед.", "Склад", "Количество"]:
                raise ValueError("Unexpected transaction headers")
            seen_header = True
            continue
        if not seen_header:
            raise ValueError("Missing transaction header row")
        counts["input_rows"] += 1
        try:
            day = datetime.strptime(values.get("A", ""), "%d.%m.%Y %H:%M:%S").date()
            quantity = number(values.get("H", ""), signed=True)
        except ValueError:
            counts["invalid_date_or_quantity_rows"] += 1
            continue
        counts["negative_rows"] += quantity < 0
        sku, unit = values.get("D"), values.get("F")
        if day < START or not sku or quantity <= 0 or not values.get("C", "").startswith("Расходная накладная"):
            counts["excluded_valid_rows"] += 1
            continue
        if unit not in UNITS or (sku in units and units[sku] != unit):
            raise ValueError("Unsupported unit or SKU changes unit")
        units[sku] = unit
        warehouses.add(values.get("G"))
        daily[(unit, sku)][day] += quantity
        counts["used_positive_invoice_rows"] += 1
    if len(warehouses) != 1 or not daily:
        raise ValueError("Expected observed sales at one warehouse")
    return dict(daily), dict(counts)


def past_group(series, origin):
    active = sorted(day for day, value in series.items() if day <= origin and value > 0)
    if not active:
        return "unseen"
    if (origin - active[0]).days + 1 < 84:
        return "new"
    if not any(day > origin - timedelta(days=84) for day in active):
        return "inactive"
    weekly = [sum((value for day, value in series.items()
                   if origin - timedelta(days=7*(i+1)) < day <= origin - timedelta(days=7*i)), ZERO)
              for i in range(24)]
    nonzero = max(1, sum(value > 0 for value in weekly))
    if Decimal(24) / nonzero >= Decimal("1.32"):
        return "intermittent"
    mean = sum(weekly, ZERO) / nonzero
    variance = sum((value*value for value in weekly), ZERO) / nonzero - mean*mean
    cv2 = max(ZERO, variance) / max(mean*mean, Decimal(1))
    return "erratic" if cv2 >= Decimal("0.49") else "regular"


def metric(rows):
    actual = sum((r["actual"] for r in rows), ZERO)
    predicted = sum((r["predicted"] for r in rows), ZERO)
    error = sum((abs(r["predicted"] - r["actual"]) for r in rows), ZERO)
    bias = predicted - actual
    return {"sku_windows": len(rows), "actual_quantity": actual, "predicted_quantity": predicted,
            "absolute_error": error, "mae": error / len(rows) if rows else None,
            "wape": error / actual if actual else None, "bias": bias / actual if actual else None,
            "bias_units": bias, "zero_actual_rows": sum(r["actual"] == 0 for r in rows),
            "absolute_error_on_zero_actual": sum((r["predicted"] for r in rows if r["actual"] == 0), ZERO)}


def summary(rows):
    result = {"overall": metric(rows)}
    for field in ("group", "origin", "abc"):
        groups = defaultdict(list)
        for row in rows:
            groups[row[field]].append(row)
        result["by_" + field] = {key: metric(values) for key, values in sorted(groups.items())}
    return result


def verify_table(path, series, unit, origins, method, selected_alias=False):
    required = ["sku", "origin", "target", "group", "abc", method] + (["selected"] if selected_alias else [])
    records = csv_records(path, required)
    expected, abc = {}, {}
    for stamp in origins:
        origin = date.fromisoformat(stamp)
        masses = []
        for (u, sku), values in series.items():
            if u != unit:
                continue
            seen = any(day <= origin for day in values)
            actual = sum((q for day, q in values.items() if origin < day <= origin + timedelta(days=28)), ZERO)
            if seen or actual > 0:
                expected[(sku, stamp)] = (actual, past_group(values, origin))
            if seen:
                mass = sum((q for day, q in values.items() if origin - timedelta(days=364) < day <= origin), ZERO)
                masses.append((sku, mass))
        total = max(sum((mass for _, mass in masses), ZERO), Decimal(13))  # rate364 denominator max(sum*28/364, 1)
        cumulative = ZERO
        for sku, mass in sorted(masses, key=lambda v: (-v[1], v[0])):
            fraction = cumulative / total
            abc[(sku, stamp)] = "A" if fraction < Decimal("0.8") else "B" if fraction < Decimal("0.95") else "C"
            cumulative += mass
    seen, scored = set(), []
    for record in records:
        key = (record["sku"], record["origin"])
        if key in seen or key not in expected:
            raise ValueError("Duplicate or unexpected SKU-window in predictions")
        seen.add(key)
        actual, group = expected[key]
        if abs(number(record["target"]) - actual) > TARGET_TOLERANCE:
            raise ValueError("Prediction target differs from raw positive observed invoices")
        if record["group"] != group or record["abc"] != abc.get(key, "unseen"):
            raise ValueError("Historical group/ABC differs from past-only independent reconstruction")
        prediction = number(record[method])
        if selected_alias and number(record["selected"]) != prediction:
            raise ValueError("Selected column differs from frozen selected method")
        if group == "unseen" and prediction != 0:
            raise ValueError("Future-only SKU must have zero forecast")
        scored.append({"actual": actual, "predicted": prediction, "origin": key[1], "group": group,
                       "abc": record["abc"]})
    if seen != set(expected):
        raise ValueError("Omitted SKU-window: failed/zero/unseen rows cannot disappear")
    return scored


def verify(inputs, artifacts, predictions):
    inputs, artifacts, predictions = map(Path, (inputs, artifacts, predictions))
    selection = json.loads((artifacts / "selection.json").read_text())
    if sha256(artifacts / "selection.json") != json.loads((artifacts / "selection.lock.json").read_text())["selection_sha256"]:
        raise ValueError("Frozen selection hash mismatch")
    required_sources = {str(path.relative_to(inputs)) for supplier in SUPPLIERS.values()
                        for pattern in ("Динамика*.xlsx", "Ежемесячные продажи*.xlsx")
                        for path in [one_file(inputs / supplier, pattern)]}
    if set(selection["source_hashes"]) != required_sources:
        raise ValueError("Frozen source map must cover exactly both transaction and monthly inputs")
    for name, expected in selection["source_hashes"].items():
        if sha256(inputs / name) != expected:
            raise ValueError("Original forecast source hash mismatch")
    report = {"kind": "independent_real_observed_sales_recalculation", "refitted": False,
              "evaluation_status": "Previously inspected July/August windows, not an independent holdout",
              "target": "Unaltered positive observed invoice shipments; returns excluded, not cleaned regular demand or net sales",
              "target_tolerance_absolute_units": str(TARGET_TOLERANCE),
              "monthly_added_to_target": False, "missing_stockout_or_clients_invented": False,
              "selection_sha256": sha256(artifacts / "selection.json"), "sources": {}, "splits": {}}
    all_series = {}
    for supplier, folder in SUPPLIERS.items():
        path = one_file(inputs / folder, "Динамика*.xlsx")
        series, counts = observed_sales(path)
        all_series[supplier] = series
        report["sources"][supplier] = {"sha256": sha256(path), "counts": counts}
    verify_panel_set(all_series, selection["panels"])
    for split, origins in ORIGINS.items():
        panels, units = {}, defaultdict(list)
        for panel, policy in selection["panels"].items():
            unit, supplier = policy["unit"], policy["supplier"]
            suffix = "validation-pairs" if split == "validation" else "evaluation"
            path = predictions / f"{panel}-{suffix}.csv"
            # The refinement table contains the selected mixture without refitting.
            scored = verify_table(path, all_series[supplier], unit, origins, policy["selected"], split == "evaluation")
            panels[panel] = {"selected_method": policy["selected"], "unit": unit, "prediction_sha256": sha256(path),
                             "raw_targets_and_row_universe_verified": True, "metrics": summary(scored)}
            units[unit].extend(scored)
        report["splits"][split] = {"panels": panels, "by_unit": {unit: summary(values) for unit, values in units.items()}}
    report["real_target_10_percent_achieved_all_panels"] = all(
        item["metrics"]["overall"]["wape"] is not None and item["metrics"]["overall"]["wape"] <= Decimal("0.1")
        for item in report["splits"]["evaluation"]["panels"].values())
    return report


def verify_panel_set(all_series, panels):
    expected = {supplier + "__" + UNITS[unit]: (supplier, unit)
                for supplier, series in all_series.items() for unit, _ in series}
    if not expected or set(panels) != set(expected):
        raise ValueError("Panel universe differs from all observed supplier/unit combinations")
    for name, (supplier, unit) in expected.items():
        if panels[name].get("supplier") != supplier or panels[name].get("unit") != unit:
            raise ValueError("Panel identity does not match its supplier/unit")


def json_number(value):
    if isinstance(value, Decimal):
        return float(value)
    raise TypeError(type(value).__name__)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", required=True, type=Path)
    parser.add_argument("--artifacts", required=True, type=Path)
    parser.add_argument("--predictions", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    with localcontext() as context:
        context.prec = 60
        report = verify(args.inputs, args.artifacts, args.predictions)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, default=json_number, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"targets_verified": True, "refitted": False,
                      "real_target_10_percent_achieved_all_panels": report["real_target_10_percent_achieved_all_panels"]}))


if __name__ == "__main__":
    main()
