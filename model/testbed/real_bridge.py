"""Export real observed-sales forecasts and source evidence for procurement review.

This bridge intentionally does not turn a point forecast into an invented demand
distribution, daily availability or an approved order. Missing business inputs
are machine-readable blockers. Row-level JSON/CSV must stay outside Git.
"""
import argparse
from collections import Counter, defaultdict
import csv
from datetime import date, timedelta
import json
from pathlib import Path

from .partner_io import SUPPLIERS, UNITS, number, one_file, private_path, sha256, workbook_rows, csv_records

BLOCKERS = ["supplier_lead_time_unknown", "review_period_unknown", "inventory_scope_and_freshness_unconfirmed",
            "demand_uncertainty_not_calibrated", "daily_demand_profile_not_supplied",
            "category_service_or_cost_policy_unknown", "purchase_unit_cost_unknown",
            "material_requirements_completeness_unknown", "inbound_completeness_and_status_unconfirmed",
            "order_constraint_units_unconfirmed"]


def constraints(path, supplier):
    code_column = "C" if supplier == "SE" else "B"
    kind = "order_multiple" if supplier == "SE" else "min_order_qty"
    expected = "Кратность" if supplier == "SE" else "Мин. разр. к отгр."
    result = defaultdict(list)
    seen_header = False
    for row, values in workbook_rows(path):
        if row == 1:
            if values.get("E") != expected or values.get(code_column) != ("Номенклатура.Код" if supplier == "SE" else "Код 1с"):
                raise ValueError("Order constraint column has changed semantics")
            seen_header = True
            continue
        if not seen_header:
            raise ValueError("Missing supplier constraint header row")
        if not values.get(code_column):
            continue
        raw = values.get("E", "")
        try:
            parsed = number(raw)
            valid = parsed > 0 if kind == "order_multiple" else parsed >= 0
        except ValueError:
            valid = False
        result[values[code_column]].append({"row": row, "field": kind,
            "value": raw if valid else None, "raw_value_valid": valid, "unit_confirmed": False})
    if not seen_header:
        raise ValueError("Missing supplier constraint header row")
    return dict(result)


def stock_snapshot(path):
    result = {}
    seen_header = False
    for row, values in workbook_rows(path, "TDSheet"):
        if row == 2:
            if [values.get(c) for c in ["C", "AX", "AY", "AZ"]] != ["Код", "Остаток", "Зарезервировано", "Свободный остаток"]:
                # Actual partner column C is Номенклатура.Код in some editions;
                # explicit accepted code aliases, never positional guessing.
                if values.get("C") not in {"Номенклатура.Код", "Код", "Код 1С", "Код 1с"} or [values.get(c) for c in ["AX", "AY", "AZ"]] != ["Остаток", "Зарезервировано", "Свободный остаток"]:
                    raise ValueError("Current stock schema mismatch")
            seen_header = True
        if row <= 2 or not values.get("C"):
            continue
        if not seen_header:
            raise ValueError("Missing current stock header row")
        sku = values["C"]
        if sku in result:
            raise ValueError("Ambiguous duplicate stock snapshot SKU")
        fields = {"on_hand": "AX", "reserved": "AY", "free_stock": "AZ", "planned_inbound": "BC"}
        parsed, invalid = {}, []
        for name, column in fields.items():
            try:
                parsed[name] = str(number(values.get(column, ""), signed=name == "free_stock"))
            except ValueError:
                parsed[name] = None
                invalid.append(name)
        consistent = (not any(parsed[k] is None for k in ("on_hand", "reserved", "free_stock"))
                      and number(parsed["on_hand"]) - number(parsed["reserved"]) == number(parsed["free_stock"], signed=True))
        result[sku] = {"row": row, "declared_snapshot_date": "2026-09-22", "source_date_basis": "filename",
                       "category_raw": values.get("E"), "stock_fields": parsed, "invalid_fields": invalid,
                       "net_equals_gross_minus_reserve": consistent, "warehouse_scope_confirmed": False,
                       "inbound_eta_label": "24.09 (year/status not confirmed)",
                       "cost_used": False, "growth_or_seasonality_coefficients_used": False}
    if not seen_header:
        raise ValueError("Missing current stock header row")
    return result


def check_forecasts(path, selection, manifest):
    records = csv_records(path, ["supplier", "unit", "sku", "origin", "warehouse", "selected_method", "forecast", "forecast_start", "forecast_end"])
    seen = set()
    if not records:
        raise ValueError("Empty forecast file")
    for row in records:
        supplier, unit = row["supplier"], row["unit"]
        if supplier not in SUPPLIERS or unit not in UNITS:
            raise ValueError("Unsupported supplier/unit")
        panel = supplier + "__" + UNITS[unit]
        if panel not in selection["panels"] or panel not in manifest["panels"]:
            raise ValueError("Forecast panel missing from saved model")
        entry, policy = manifest["panels"][panel], selection["panels"][panel]
        if row["selected_method"] != policy["selected"] or row["selected_method"] != entry["selected"]:
            raise ValueError("Forecast method differs from saved selection")
        origin = date.fromisoformat(row["origin"])
        if row["origin"] != origin.isoformat() or origin < date.fromisoformat(entry["trained_as_of"]):
            raise ValueError("Final weights cannot backcast before training cutoff")
        if row["forecast_start"] != (origin + timedelta(days=1)).isoformat() or row["forecast_end"] != (origin + timedelta(days=28)).isoformat():
            raise ValueError("Forecast dates must span origin+1 through origin+28")
        number(row["forecast"])
        key = supplier, unit, row["sku"], row["origin"]
        if key in seen or not row["sku"] or not row["warehouse"]:
            raise ValueError("Duplicate forecast or missing identity/scope")
        seen.add(key)
    return records


def csv_safe(value):
    text = str(value)
    return "'" + text if text.startswith(("=", "+", "-", "@", "\t", "\r", "\n")) else text


def build_packet(records, lots, snapshots):
    items = []
    for row in sorted(records, key=lambda r: (r["supplier"], r["unit"], r["sku"])):
        supplier, sku = row["supplier"], row["sku"]
        evidence = lots.get(supplier, {}).get(sku, [])
        snapshot = snapshots.get(sku) if supplier == "SE" else None
        missing = list(BLOCKERS)
        missing.append("minimum_order_quantity_unknown" if supplier == "SE" else "order_multiple_unknown")
        if not evidence:
            missing.append("supplier_constraint_not_matched")
        elif len(evidence) != 1:
            missing.append("duplicate_supplier_constraint_requires_review")
        if any(not e["raw_value_valid"] for e in evidence):
            missing.append("invalid_supplier_constraint")
        if snapshot is None:
            missing.append("current_stock_snapshot_not_matched")
        else:
            if snapshot["declared_snapshot_date"] > row["origin"]:
                missing.append("snapshot_after_forecast_origin")
            if snapshot["invalid_fields"] or not snapshot["net_equals_gross_minus_reserve"]:
                missing.append("inventory_snapshot_inconsistent_or_incomplete")
        items.append({"sku": sku, "supplier_id": supplier, "unit": row["unit"], "warehouse": row["warehouse"],
                      "forecast_origin": row["origin"], "forecast_start": row["forecast_start"], "forecast_end": row["forecast_end"],
                      "observed_sales_forecast_28d": row["forecast"], "method": row["selected_method"],
                      "target_semantics": "positive observed invoice shipments, not recovered uncensored demand",
                      "status": "needs_data", "quantity": None, "urgency": "data_required",
                      "requires_manual_approval": True, "approval_blocked": True, "missing": missing,
                      "supplier_constraint_evidence": evidence, "current_snapshot_evidence": snapshot,
                      "reason": "28-day observed-sales forecast is available. Order quantity is blocked: " + ", ".join(missing)})
    groups = defaultdict(list)
    for item in items:
        groups[item["supplier_id"]].append(item)
    return {"schema": "real-procurement-review-v1", "kind": "forecast_and_source_review_not_an_order",
            "approval_blocked": True, "requires_manual_approval": True,
            "suppliers": [{"supplier_id": key, "items": values} for key, values in sorted(groups.items())]}


def export(inputs, artifacts, forecasts, private, report_path):
    inputs, artifacts = Path(inputs), Path(artifacts)
    private = private_path(private)
    private.mkdir(parents=True, exist_ok=True)
    selection = json.loads((artifacts / "selection.json").read_text())
    manifest = json.loads((artifacts / "manifest.json").read_text())
    if manifest["selection_sha256"] != sha256(artifacts / "selection.json") or json.loads((artifacts / "selection.lock.json").read_text())["selection_sha256"] != manifest["selection_sha256"]:
        raise ValueError("Model selection hash mismatch")
    required_sources = {str(path.relative_to(inputs)) for supplier in SUPPLIERS.values()
                        for pattern in ("Динамика*.xlsx", "Ежемесячные продажи*.xlsx")
                        for path in [one_file(inputs / supplier, pattern)]}
    if set(selection["source_hashes"]) != required_sources:
        raise ValueError("Saved selection does not cover the supplied real inputs")
    for name, expected in selection["source_hashes"].items():
        if sha256(inputs / name) != expected:
            raise ValueError("Real input differs from saved source provenance")
    for panel in manifest["panels"].values():
        for model in panel["models"].values():
            if sha256(artifacts / model["file"]) != model["sha256"]:
                raise ValueError("Saved weight checksum mismatch")
    records = check_forecasts(forecasts, selection, manifest)
    lots, sources = {}, {}
    for supplier, folder in SUPPLIERS.items():
        path = one_file(inputs / folder, "MOQ*.xlsx")
        lots[supplier] = constraints(path, supplier)
        sources[supplier + "_constraints"] = {"sha256": sha256(path)}
    path = one_file(inputs / SUPPLIERS["SE"], "Товар в пути*.xlsx")
    if "22.09.2026" not in path.name:
        raise ValueError("Snapshot date must be explicitly supported, not guessed")
    snapshots = stock_snapshot(path)
    sources["SE_snapshot"] = {"sha256": sha256(path)}
    packet = build_packet(records, lots, snapshots)
    items = [item for group in packet["suppliers"] for item in group["items"]]
    packet["source_hashes"] = sources
    packet["forecast_sha256"] = sha256(forecasts)
    target = private_path(private / "procurement-review.json")
    target.write_text(json.dumps(packet, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    fields = ["supplier_id", "sku", "unit", "forecast_origin", "forecast_start", "forecast_end",
              "observed_sales_forecast_28d", "quantity", "status", "urgency", "requires_manual_approval", "reason"]
    with private_path(private / "procurement-review.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for item in items:
            writer.writerow({key: "" if item[key] is None else csv_safe(item[key]) for key in fields})
    report = {"kind": packet["kind"], "forecast_sha256": packet["forecast_sha256"], "source_hashes": sources,
              "rows": len(items), "supplier_groups": len(packet["suppliers"]),
              "by_supplier_unit": dict(Counter(i["supplier_id"] + "/" + i["unit"] for i in items)),
              "needs_data": len(items), "approved_orders": 0, "automatic_dispatches": 0,
              "constraint_matches": sum(bool(i["supplier_constraint_evidence"]) for i in items),
              "current_snapshot_matches": sum(i["current_snapshot_evidence"] is not None for i in items),
              "missing_counts": dict(Counter(key for i in items for key in i["missing"])),
              "raw_partner_rows_in_this_report": False,
              "limitations": ["CSV forecast provenance relies on the separate replay command; schema/hash checks alone do not prove execution.",
                              "IEK inbound is not applied: stock, shipment status and unit conversion are unconfirmed.",
                              "SE cost/growth/seasonality fields and both current supplier categories are not past forecast features.",
                              "No calibrated demand distribution or inferred daily availability; monthly stock is not stockout history.",
                              "No claimed 1C import compatibility or release-ready real order."]}
    report_path = Path(report_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for field in ("inputs", "artifacts", "forecasts", "private", "report"):
        parser.add_argument("--" + field, required=True, type=Path)
    args = parser.parse_args()
    report = export(args.inputs, args.artifacts, args.forecasts, args.private, args.report)
    print(json.dumps({key: report[key] for key in ("rows", "supplier_groups", "needs_data", "approved_orders")}))


if __name__ == "__main__":
    main()
