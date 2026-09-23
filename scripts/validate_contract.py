"""Validate the proposed API and synthetic examples; this does not test a server."""

import json
import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator, FormatChecker
from openapi_spec_validator import validate


ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "contracts"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def main():
    spec = yaml.safe_load((CONTRACTS / "openapi.yaml").read_text(encoding="utf-8"))
    validate(spec)
    monitoring = yaml.safe_load((CONTRACTS / "stock-monitoring.openapi.yaml").read_text(encoding="utf-8"))
    validate(monitoring)
    for name, schema_name in (("event", "StockEvent"), ("result", "EventResult")):
        payload = json.loads((CONTRACTS / "monitoring-examples" / f"{name}.json").read_text(encoding="utf-8"))
        Draft202012Validator(
            {"$ref": f"#/components/schemas/{schema_name}", "components": monitoring["components"]},
            format_checker=FormatChecker(),
        ).validate(payload)
    mapping = json.loads((CONTRACTS / "example-schemas.json").read_text(encoding="utf-8"))
    examples_dir = CONTRACTS / "examples"
    require(
        set(mapping) == {p.name for p in examples_dir.glob("*.json")},
        "Every JSON example must have exactly one schema mapping",
    )
    examples = {}
    for filename, schema_name in mapping.items():
        payload = json.loads((examples_dir / filename).read_text(encoding="utf-8"))
        schema = {
            "$ref": f"#/components/schemas/{schema_name}",
            "components": spec["components"],
        }
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(payload)
        examples[filename] = payload

    request = examples["calculation-request.json"]
    report = examples["dataset-report.json"]
    result = examples["recommendations.json"]
    detail = examples["item-detail.json"]
    approval = examples["approval.json"]
    job = examples["job-succeeded.json"]
    meta = result["meta"]
    items = {item["item_id"]: item for item in result["items"]}
    require(len(items) == len(result["items"]), "Duplicate item IDs")
    require(request["mode"] == meta["mode"] == approval["mode"] == "scenario", "Synthetic mode")
    require(request["dataset_id"] == report["dataset_id"] == meta["dataset_id"], "Dataset IDs")
    require(report["dataset_version"] == meta["dataset_version"], "Dataset versions")
    require(request["horizon_days"] == request["lead_time_days"] + request["review_period_days"], "H=L+R")
    require(meta["horizon_days"] == request["horizon_days"], "Forecast horizon")
    require(detail["meta"] == meta and detail["item"] == items[detail["item"]["item_id"]], "Detail consistency")
    require(job["status"] == "succeeded" and job["error"] is None, "Job success state")
    require(job["resource_type"] == "calculation" and job["resource_id"] == meta["calculation_id"], "Job resource")
    require(approval["calculation_id"] == meta["calculation_id"], "Approval calculation")
    require(approval["revision"] == meta["revision"], "Approval revision")
    require(approval["selected_item_ids"] == examples["approval-request.json"]["selected_item_ids"], "Approval selection")

    known_cost = Decimal(0)
    for item in items.values():
        forecast = item["forecast"]
        if forecast is not None:
            require(forecast["method"] == "contract_example", "Samples must not claim trained ML")
            require(forecast["p10"] <= forecast["p50"] <= forecast["p90"], "Quantile order")
            days = (date.fromisoformat(forecast["period_end"]) - date.fromisoformat(forecast["period_start"])).days + 1
            require(days == forecast["horizon_days"] == meta["horizon_days"], "Forecast date range")
        if item["decision_status"] == "needs_data":
            require(item["recommended_quantity"] is None and item["final_quantity"] is None, "Missing inputs require null quantity")
        if item["final_quantity"] is not None and item["final_quantity"] > 0:
            quantity = Decimal(str(item["final_quantity"]))
            multiple = item["order_multiple"]
            if multiple is not None:
                require(quantity % Decimal(str(multiple)) == 0, "Order multiple")
            if item["min_order_qty"] is not None:
                require(quantity >= Decimal(str(item["min_order_qty"])), "Minimum order quantity")
            if item["unit_cost_kzt"] is not None:
                cost = quantity * Decimal(str(item["unit_cost_kzt"]))
                require(cost == Decimal(str(item["order_cost_kzt"])), "Order cost")
        if item["order_cost_kzt"] is not None:
            known_cost += Decimal(str(item["order_cost_kzt"]))
        require(item["ai"]["status"] == "not_requested" and item["ai"]["verdict"] is None, "No fabricated LLM review")

    summary = result["summary"]
    require(summary["item_count"] == len(items), "Summary item count")
    for status in ("ready", "no_order", "needs_data", "needs_review"):
        require(summary[f"{status}_count"] == sum(item["decision_status"] == status for item in items.values()), "Summary status count")
    require(known_cost == Decimal(str(summary["known_order_cost_kzt"])), "Summary cost")
    require(summary["order_cost_complete"] == all(item["order_cost_kzt"] is not None for item in items.values()), "Incomplete cost flag")
    selected_cost = Decimal(0)
    for item_id in approval["selected_item_ids"]:
        require(item_id in items, "Unknown approved item")
        item = items[item_id]
        require(item["decision_status"] == "ready" and item["final_quantity"] is not None, "Unapprovable example item")
        selected_cost += Decimal(str(item["order_cost_kzt"]))
    require(request["budget_kzt"] is None or selected_cost <= Decimal(str(request["budget_kzt"])), "Approval budget")

    markdown_files = [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md")), CONTRACTS / "README.md", ROOT / "simulation" / "README.md"]
    for document in markdown_files:
        content = document.read_text(encoding="utf-8")
        for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", content):
            if target.startswith(("http://", "https://", "#", "mailto:")):
                continue
            path = target.split("#", 1)[0]
            require((document.parent / path).exists(), f"Broken link in {document.relative_to(ROOT)}: {target}")

    print(f"OK: two OpenAPI specs, {len(examples) + 2} schema-valid examples, sample consistency and {len(markdown_files)} Markdown files.")
    print("No application, trained model or HTTP server was tested.")


if __name__ == "__main__":
    main()
