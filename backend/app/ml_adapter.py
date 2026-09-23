"""Built-in bridge from uploaded SE workbooks to forecast v2 and decision core.

The adapter keeps partner workbooks in DATA_DIR, records their provenance and
returns the existing public API contract. It does not call an LLM or send orders.
"""
from copy import deepcopy
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from hashlib import sha256 as hashlib_sha256
import json
import os
from pathlib import Path
import shutil

import numpy as np
import pandas as pd

from model.decision.core import (
    EconomicProfile, GrowthAdjustment, Inbound, InventorySnapshot,
    MaterialRequirement, Recommendation, RecommendationInput, ServicePolicy,
    SupplierConstraint, recommend,
)
from model.forecast_v2.data import load_panels, sha256
from model.forecast_v2.features import features_at
from model.forecast_v2.methods import MODEL_SPECS, load_model
from model.forecast_v2.run import forecast_methods, verify_prediction_implementation
from model.lab.sources import PATH_PATTERNS, load_se

from .contracts import DomainError, ROOT
from .demo import now, summary


MODEL_DIR = ROOT / "model" / "forecast_v2" / "artifacts"
ROLE_NAMES = {
    "sales_transactions", "sales_monthly", "stock_monthly", "seasonality",
    "moq", "current_stock_inbound",
}


def _json_default(value):
    if isinstance(value, (np.integer, np.floating)):
        return value.item()
    raise TypeError(f"Cannot encode {type(value).__name__}")


def _write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2,
                                    allow_nan=False, default=_json_default) + "\n", encoding="utf-8")


def _issue(code, severity, message, skus=(), reference=None):
    return {"code": code, "severity": severity, "message": message,
            "affected_skus": list(skus), "source_reference": reference}


def _prepare_model_inputs(directory, report):
    """Create local aliases with original safe names; source bytes stay unchanged."""
    target = Path(directory) / "model_inputs" / "Systeme electric"
    target.mkdir(parents=True, exist_ok=True)
    by_role = {source["role"]: source for source in report["sources"]}
    if set(by_role) != ROLE_NAMES:
        raise ValueError("All six Systeme Electric source roles are required for normalization")
    for role, source in by_role.items():
        original = Path(source["filename"]).name
        if not original or original in (".", ".."):
            raise ValueError("Unsafe original source filename")
        if not Path(original).match(PATH_PATTERNS[role]):
            raise ValueError(
                f"Filename for {role} must preserve the partner export pattern "
                f"{PATH_PATTERNS[role]!r}; the inventory filename must contain its snapshot date"
            )
        source_path, alias = Path(directory) / f"{role}.xlsx", target / original
        if sha256(source_path) != source["sha256"]:
            raise ValueError(f"Stored source hash differs for {role}")
        if alias.exists():
            if sha256(alias) != source["sha256"]:
                raise ValueError(f"Existing model input alias differs for {role}")
            continue
        try:
            os.link(source_path, alias)
        except OSError:
            shutil.copy2(source_path, alias)
    return target.parent


def normalize_dataset(directory: Path, report: dict, context: dict | None) -> dict:
    """Normalize and audit all six uploaded workbooks for the platform import job."""
    if report['supplier_ids'] == ['iek']:
        from .iek_adapter import normalize_dataset as normalize_iek
        return normalize_iek(directory, report, context)
    directory = Path(directory)
    roles = {source["role"] for source in report["sources"]}
    if roles != ROLE_NAMES:
        report["issues"] = [issue for issue in report["issues"] if issue["code"] != "NORMALIZATION_REQUIRED"]
        report["issues"].append(_issue(
            "NORMALIZATION_BLOCKED", "error",
            "Для модели нужны все шесть ролей файлов; набор сохранён, расчёт не разрешён.",
            reference="backend.app.ml_adapter"))
        report["calculation_allowed"] = False
        return report
    try:
        model_root = _prepare_model_inputs(directory, report)
        sources = load_se(model_root / "Systeme electric")
    except (ValueError, OSError) as exc:
        raise DomainError("INVALID_FILE", f"Нормализация не завершена: {exc}", 422) from exc

    audit = sources["audit"]
    normalized = directory / "normalized"
    normalized.mkdir(exist_ok=True)
    _write_json(normalized / "current.json", sources["current"])
    _write_json(normalized / "audit.json", audit)
    _write_json(normalized / "context.json", context or {})
    _write_json(normalized / "metadata.json", {
        "model_input_root": str(model_root.relative_to(directory)),
        "data_as_of": audit["current_snapshot"]["as_of"],
        "forecast_sales_as_of": audit["transactions"]["data_as_of"],
        "synthetic_context": any(
            row.get("source_kind") == "synthetic"
            for key in ("stockout_intervals", "price_observations", "material_requirements")
            for row in (context or {}).get(key, [])
        ),
        "normalizer": "backend.app.ml_adapter:v1",
    })

    used = {
        "sales_transactions": audit["transactions"]["used_rows"],
        "sales_monthly": audit["monthly_sales"]["sku_count"],
        "stock_monthly": audit["monthly_stock"]["sku_count"],
        "seasonality": 3,  # three annual source rows; raw_months is a cell count
        "moq": audit["moq_profiles"],
        "current_stock_inbound": audit["current_profiles"],
    }
    dated = {
        "sales_transactions": audit["transactions"]["data_as_of"],
        "current_stock_inbound": audit["current_snapshot"]["as_of"],
    }
    for source in report["sources"]:
        source["rows_used"] = int(used[source["role"]])
        source["data_as_of"] = dated.get(source["role"])

    missing_profiles = audit["coverage"]["current_profile"]["daily_skus_missing"]
    report.update(
        dataset_version=hashlib_sha256((report["dataset_version"] + "|normalized-v1").encode()).hexdigest()[:12],
        data_as_of=audit["current_snapshot"]["as_of"],
        sku_count=audit["transactions"]["sku_count"],
        calculation_allowed=True,
    )
    report["issues"] = [issue for issue in report["issues"]
                        if issue["code"] not in {"NORMALIZATION_REQUIRED", "SOURCE_DATE_UNKNOWN"}]
    context = context or {}
    synthetic_context = any(
        row.get("source_kind") == "synthetic"
        for key in ("stockout_intervals", "price_observations", "material_requirements")
        for row in context.get(key, [])
    )
    reconciliation = audit["daily_monthly_reconciliation"]
    unknown_units = audit["current_snapshot"]["unit_counts"].get("unknown", 0)
    missing_multiples = audit["current_snapshot"]["missing_multiple"]
    report["issues"].extend([
        _issue("CURRENT_STOCK_PARTIAL", "warning",
               f"У {missing_profiles} продававшихся SKU нет текущего профиля; для них количество останется неизвестным.",
               reference="normalized/audit.json"),
        _issue("CURRENT_PROFILE_WITHOUT_HISTORY_EXCLUDED", "warning",
               f"{audit['coverage']['current_profile']['additional_skus']} SKU текущего снимка не имеют дневной истории "
               "в подтверждённой единице и не включены в список заказа.",
               reference="normalized/audit.json"),
        _issue("CONTEXT_STOCKOUT_NOT_APPLIED" if context.get("stockout_intervals") else "NO_DAILY_STOCKOUT", "warning",
               "Переданные интервалы stockout пока не входят в frozen forecast; скрытый спрос не восстановлен."
               if context.get("stockout_intervals") else
               "Точных интервалов отсутствия товара нет; скрытый спрос на реальных данных не восстановлен.",
               reference="normalized/audit.json"),
        _issue("CONTEXT_CLIENT_LABELS_NOT_APPLIED" if context.get("client_labels") else "NO_CLIENT_LABELS", "warning",
               "Переданные клиентские метки пока не связаны со строками исходной выгрузки; клиентские выбросы не классифицированы."
               if context.get("client_labels") else
               "В исходной выгрузке нет обезличенных клиентов; клиентские разовые заказы нельзя подтвердить на реальных данных.",
               reference="normalized/audit.json"),
        _issue("LEAD_TIME_FROM_REQUEST", "info",
               "Срок новой поставки берётся из параметров расчёта, поскольку справочник lead time не предоставлен.",
               reference="CalculationRequest.lead_time_days"),
        _issue("DAILY_MONTHLY_MISMATCH", "warning",
               f"Дневная и месячная выгрузки расходятся в {reconciliation['differing_sku_months']} из "
               f"{reconciliation['compared_complete_sku_months']} сопоставимых SKU-месяцев; они не суммируются.",
               reference="normalized/audit.json"),
        _issue("NEGATIVE_SALES_EXCLUDED", "warning",
               f"Исключены {audit['transactions']['negative_rows']} отрицательных строк и "
               f"{audit['monthly_sales']['negative_cells_excluded']} отрицательных месячных ячеек.",
               reference="normalized/audit.json"),
        _issue("UNKNOWN_UNIT", "warning", f"У {unknown_units} текущих SKU не подтверждена единица.",
               reference="normalized/audit.json"),
        _issue("UNKNOWN_ORDER_MULTIPLE", "warning", f"У {missing_multiples} текущих SKU неизвестна кратность.",
               reference="normalized/audit.json"),
        _issue("UNKNOWN_MIN_ORDER_QTY", "warning", "MOQ не предоставлен ни для одного SKU; утверждение партии блокируется.",
               reference="normalized/audit.json"),
        _issue("UNVERIFIED_WAREHOUSE_SCOPE", "warning",
               "Область складского снимка не подтверждена; количество остаётся предварительным.",
               reference="normalized/audit.json"),
        _issue("UNVERIFIED_COST", "warning",
               "Поле себестоимости исходного снимка не имеет подтверждённого смысла и не используется.",
               reference="normalized/audit.json"),
        _issue("SOURCE_EFFECTIVE_DATE_UNKNOWN", "warning",
               "Для месячных источников, сезонности и кратности нет дат публикации/вступления в силу.",
               reference="normalized/audit.json"),
        _issue("SYNTHETIC_CONTEXT_BLOCKS_OPERATIONAL", "warning",
               "Синтетический дополнительный контекст разрешён только в сценарном режиме.",
               reference="normalized/context.json") if synthetic_context else
        _issue("CATEGORY_POLICY_REQUIRED", "info",
               "Для SKU без экономического профиля нужна явная политика категории.",
               reference="CalculationRequest.category_policies"),
    ])
    if synthetic_context:
        report["issues"].append(_issue(
            "CATEGORY_POLICY_REQUIRED", "info",
            "Для SKU без экономического профиля нужна явная политика категории.",
            reference="CalculationRequest.category_policies"))
    return report


def _load_forecast(panel, origin):
    selection = json.loads((MODEL_DIR / "selection.json").read_text(encoding="utf-8"))
    lock = json.loads((MODEL_DIR / "selection.lock.json").read_text(encoding="utf-8"))
    manifest = json.loads((MODEL_DIR / "manifest.json").read_text(encoding="utf-8"))
    if sha256(MODEL_DIR / "selection.json") != lock["selection_sha256"]:
        raise ValueError("Forecast selection lock mismatch")
    verify_prediction_implementation(selection)
    if manifest["selection_sha256"] != lock["selection_sha256"] or manifest["model_specs"] != MODEL_SPECS:
        raise ValueError("Forecast manifest is incompatible with frozen selection")
    entry = manifest["panels"][panel.name]
    if pd.Timestamp(origin) < pd.Timestamp(entry["trained_as_of"]):
        raise ValueError("Final weights cannot be used before their training cutoff")
    current = features_at(panel, pd.Timestamp(origin))
    weights = {}
    for name, meta in entry["models"].items():
        path = MODEL_DIR / meta["file"]
        if sha256(path) != meta["sha256"]:
            raise ValueError(f"Forecast weight hash mismatch: {meta['file']}")
        weights[name] = load_model(name, path)
    predictions, _ = forecast_methods(panel, current, [entry["selected"], entry["best_ml"], "v1_baseline"],
                                      pd.DataFrame(), pd.DataFrame(), 1, 1, weights)
    return current, predictions[entry["selected"]], entry, lock["selection_sha256"]


def _daily_path(panel, sku, origin, total):
    """Allocate a 28-day total by observed weekday shape; this is a model trajectory."""
    history = panel.daily.loc[sku, :pd.Timestamp(origin)].iloc[-84:]
    weekday = history.groupby(history.index.weekday).mean().reindex(range(7), fill_value=0).to_numpy(float)
    future = pd.date_range(pd.Timestamp(origin) + pd.Timedelta(days=1), periods=28)
    weights = np.asarray([weekday[d.weekday()] for d in future], dtype=float)
    if not np.isfinite(weights).all() or weights.sum() <= 0:
        weights = np.ones(28)
    values = weights / weights.sum() * float(total)
    return values.tolist()


def _context_maps(context, as_of, warehouse_id):
    context = context or {}
    prices = {}
    for row in context.get("price_observations", []):
        if row["effective_date"] <= as_of and (row["sku"] not in prices or row["effective_date"] > prices[row["sku"]]["effective_date"]):
            prices[row["sku"]] = row
    materials = {}
    for row in context.get("material_requirements", []):
        if row["warehouse_id"] == warehouse_id:
            materials.setdefault(row["sku"], []).append(row)
    return prices, materials


def _history(panel, sku, origin):
    end = pd.Timestamp(origin)
    values = []
    for offset in range(5, -1, -1):
        period_end = end - pd.Timedelta(days=offset * 28)
        period_start = period_end - pd.Timedelta(days=27)
        observed = float(panel.daily.loc[sku, period_start:period_end].sum())
        values.append({"period_start": str(period_start.date()), "period_end": str(period_end.date()),
                       "observed_sales": observed, "regular_sales": None,
                       "estimated_lost_demand": None, "source_kind": "observed"})
    return values


def _policy_maps(request):
    policies = {}
    for row in request["category_policies"]:
        if row["category_raw"] in policies:
            raise DomainError("INVALID_PARAMETERS", "Политика категории передана дважды.")
        policies[row["category_raw"]] = row
    economics = {}
    for row in request["economic_profiles"]:
        if row["sku"] in economics:
            raise DomainError("INVALID_PARAMETERS", "Экономический профиль SKU передан дважды.")
        economics[row["sku"]] = row
    growth = {}
    for row in request["growth_adjustments"]:
        growth.setdefault(row["sku"], []).append(row)
    return policies, economics, growth


class TirekCalculationPipeline:
    """Use frozen forecast weights and the production decision core."""

    def calculate(self, dataset: dict, request: dict, calculation_id: str) -> dict:
        if dataset.get('supplier_ids') == ['iek']:
            from .iek_adapter import IEKForecastPipeline
            return IEKForecastPipeline().calculate(dataset, request, calculation_id)
        data_dir = Path(os.getenv("DATA_DIR", str(ROOT / "data")))
        directory = data_dir / "uploads" / dataset["dataset_id"]
        metadata = json.loads((directory / "normalized" / "metadata.json").read_text(encoding="utf-8"))
        current_profiles = json.loads((directory / "normalized" / "current.json").read_text(encoding="utf-8"))
        context = json.loads((directory / "normalized" / "context.json").read_text(encoding="utf-8"))
        panels, _ = load_panels(directory / metadata["model_input_root"], ("SE",))
        panel = next(value for value in panels if value.name == "SE__pieces")
        origin = pd.Timestamp(request["as_of_date"])
        if origin > panel.daily.columns[-1]:
            raise DomainError("MISSING_CRITICAL_DATA", "История продаж не доходит до даты расчёта.")
        frame, point_forecasts, model_entry, selection_hash = _load_forecast(panel, origin)
        policies, economics, growth = _policy_maps(request)
        prices, material_rows = _context_maps(context, request["as_of_date"], "almaty")
        meta = {
            "calculation_id": calculation_id, "dataset_id": dataset["dataset_id"],
            "dataset_version": dataset["dataset_version"], "data_as_of": dataset["data_as_of"],
            "as_of_date": request["as_of_date"], "mode": request["mode"], "revision": 1,
            "policy_version": hashlib_sha256(json.dumps({k: request[k] for k in (
                "category_policies", "economic_profiles", "growth_adjustments")}, sort_keys=True).encode()).hexdigest()[:12],
            "created_at": now(), "horizon_days": request["horizon_days"], "issues": deepcopy(dataset["issues"]),
        }
        details, approval_constraints = {}, {}
        for position, row in frame.reset_index(drop=True).iterrows():
            sku, forecast_total = str(row["sku"]), float(point_forecasts[position])
            profile = current_profiles.get(sku)
            category = profile.get("category") if profile else None
            if request["category_codes"] and category not in request["category_codes"]:
                continue
            if request["warehouse_ids"] and not set(request["warehouse_ids"]) & {"almaty", "Алматы"}:
                continue
            inventory = None
            if profile and profile.get("inventory_as_of") <= request["as_of_date"]:
                inventory = InventorySnapshot(date.fromisoformat(profile["inventory_as_of"]),
                                              profile.get("on_hand"), profile.get("reserved"),
                                              profile.get("free_stock"), profile.get("source", "snapshot"))
            inbound = []
            if profile and profile.get("inbound_quantity") is not None and profile.get("inbound_eta"):
                inbound.append(Inbound(f"inbound-{sku}", profile["inbound_quantity"],
                                       date.fromisoformat(profile["inbound_eta"]), "шт", profile["source"]))
            constraint = None
            if profile:
                constraint = SupplierConstraint("шт", None, profile.get("order_multiple"), 1,
                                                profile.get("source", "supplier export"))
            econ_row = economics.get(sku)
            price = prices.get(sku)
            econ = EconomicProfile(econ_row["underage_cost"], econ_row["overage_cost"],
                                   econ_row["horizon_days"], econ_row["currency"], econ_row["rationale"]) if econ_row else None
            policy_row = policies.get(category)
            policy = None
            if policy_row:
                # With economics, target_quantile is only a fallback. Only an
                # explicit minimum_target_quantile may constrain the economic choice.
                level = policy_row["minimum_target_quantile"] if econ else policy_row["target_quantile"]
                if level is not None:
                    policy = ServicePolicy(level, policy_row["rationale"], meta["policy_version"])
            adjustments = [GrowthAdjustment(f"growth-{sku}-{index}", value["rate"],
                                             date.fromisoformat(value["valid_from"]), date.fromisoformat(value["valid_to"]),
                                             value["rationale"]) for index, value in enumerate(growth.get(sku, []))]
            materials = []
            for value in material_rows.get(sku, []):
                uncovered = float(Decimal(str(value["quantity"])) - Decimal(str(value["already_accounted_quantity"])))
                materials.append(MaterialRequirement(value["requirement_id"], uncovered,
                                                     date.fromisoformat(value["needed_at"]), value["unit"],
                                                     value["reference"], hard=True))
            unit_cost = (econ_row.get("unit_cost") if econ_row and econ_row.get("unit_cost") is not None
                         else price.get("unit_cost") if price else None)
            mapped_policies = {category: policy} if category is not None and policy is not None else {}
            decision_input = RecommendationInput(
                sku=sku, warehouse_id="almaty", supplier_id="systeme-electric", unit="шт",
                as_of=date.fromisoformat(request["as_of_date"]), forecast_as_of=date.fromisoformat(request["as_of_date"]),
                scenarios=[_daily_path(panel, sku, origin, forecast_total)],
                forecast_id=f"forecast-v2-{selection_hash[:12]}", unit_quantum=1,
                inventory=inventory, lead_time_days=request["lead_time_days"],
                review_period_days=request["review_period_days"], constraints=constraint,
                economics=econ, service_policy=None,
                category_id=category if mapped_policies else None, category_policies=mapped_policies,
                inbound=inbound, materials=materials, growth_adjustments=adjustments,
                unit_cost=unit_cost, price_currency="KZT" if unit_cost is not None else None,
                data_version=dataset["dataset_version"], max_snapshot_age_days=0, max_forecast_age_days=0,
            )
            try:
                result = recommend(decision_input)
            except ValueError as exc:
                result = Recommendation(sku, "almaty", "systeme-electric", "шт",
                                        date.fromisoformat(request["as_of_date"]), "needs_data", None, None,
                                        f"Расчёт отклонён из-за качества входа: {exc}", "unknown", (),
                                        ("valid_source_values",), unit_cost, "KZT" if unit_cost is not None else None,
                                        {"free_stock": profile.get("free_stock") if profile else None,
                                         "inbound_in_horizon": 0, "dated_material_demand": 0,
                                         "unit_quantum": 1, "min_order_qty_inventory_units": None,
                                         "order_multiple_inventory_units": profile.get("order_multiple") if profile else None,
                                        "service_floor_quantity": 0, "hard_material_floor_quantity": 0})
            if profile and not profile.get("warehouse_scope_verified", False) and result.quantity is not None:
                result = replace(
                    result, status="needs_review", quantity=None,
                    provisional_quantity=result.quantity,
                    warnings=tuple(dict.fromkeys((*result.warnings, "warehouse_scope_unverified"))),
                    required_fields=tuple(dict.fromkeys((*result.required_fields, "warehouse_scope_confirmation"))),
                )
            item_id = "se-" + hashlib_sha256(sku.encode()).hexdigest()[:12]
            shown_quantity = result.quantity if result.quantity is not None else result.provisional_quantity
            decision_status = "no_order" if result.status == "ready" and shown_quantity == 0 else result.status
            issue_rows = [_issue("REQUIRED_" + field.upper().replace(":", "_"), "error",
                                 "Нужно уточнить: " + field, [sku], profile.get("source") if profile else None)
                          for field in result.required_fields]
            issue_rows += [_issue(value.upper(), "warning", value.replace("_", " "), [sku], "decision-core-1")
                           for value in result.warnings]
            if result.provisional_quantity is not None:
                issue_rows.append(_issue("PROVISIONAL_QUANTITY", "warning",
                                         "Показана предварительная потребность; неизвестные условия партии блокируют утверждение.",
                                         [sku], "decision-core-1"))
            forecast = {
                "horizon_days": 28, "period_start": str((origin + pd.Timedelta(days=1)).date()),
                "period_end": str((origin + pd.Timedelta(days=28)).date()), "method": "ml",
                "model_id": f"forecast-v2-{selection_hash[:12]}", "mean": forecast_total,
                "p10": None, "p50": None, "p90": None,
                "target_quantile": policy.service_level if policy else None, "target_stock": None,
                "calibration_status": "insufficient_data",
                "note": "Точечный прогноз наблюдаемых продаж; дневная траектория оценена по прошлым дням недели. Квантили не калиброваны.",
            }
            selected_method = model_entry["selected"]
            forecast["method"] = "baseline" if selected_method.startswith(("mean", "median", "snaive", "croston", "tsb")) else "ml"
            order_cost = None if shown_quantity is None or unit_cost is None else float(Decimal(str(shown_quantity)) * Decimal(str(unit_cost)))
            ai = {"status": "unavailable" if request["request_ai_review"] else "not_requested", "verdict": None,
                  "reasons": ["LLM-провайдер не настроен; детерминированный расчёт сохранён."] if request["request_ai_review"] else [],
                  "evidence_ids": [], "rule_ids": ["AI-01"] if request["request_ai_review"] else [],
                  "suggested_action": None, "provider_model": None}
            evidence = [
                {"id": f"{item_id}-forecast", "source_kind": "observed", "reference": f"forecast-v2:{selection_hash}",
                 "label": "Прогноз на 28 дней", "value": forecast_total, "unit": "шт"},
                {"id": f"{item_id}-stock", "source_kind": "observed", "reference": profile.get("source") if profile else "missing-current-profile",
                 "label": "Свободный остаток", "value": result.diagnostics.get("free_stock"), "unit": "шт"},
            ]
            item = {
                "item_id": item_id, "supplier_id": "systeme-electric", "supplier_name": "Systeme Electric",
                "sku": sku, "supplier_article": profile.get("supplier_article") if profile else None,
                "name": profile.get("name", sku) if profile else sku, "unit": "шт", "warehouse_id": "almaty",
                "category_raw": category, "policy_basis": "economic" if econ else "service_policy" if policy else None,
                "decision_status": decision_status, "urgency": "critical" if result.urgency == "expedite" else result.urgency,
                "free_stock": result.diagnostics.get("free_stock"),
                "inbound_within_horizon": result.diagnostics.get("inbound_in_horizon"),
                "material_requirement_uncovered": result.diagnostics.get("dated_material_demand"),
                "forecast": forecast, "min_order_qty": result.diagnostics.get("min_order_qty_inventory_units"),
                "order_multiple": result.diagnostics.get("order_multiple_inventory_units"),
                "recommended_quantity": shown_quantity, "final_quantity": result.quantity,
                "override_reason": None, "unit_cost_kzt": unit_cost, "order_cost_kzt": order_cost if result.quantity is not None else None,
                "marginal_value": None, "economics_source": econ_row["source_kind"] if econ_row else None,
                "reason": result.reason, "issues": issue_rows, "evidence": evidence, "ai": ai,
            }
            applied = (["POLICY-01"] if econ else ["POLICY-02"] if policy else ["DATA-01"])
            details[item_id] = {"meta": meta, "item": item, "history": _history(panel, sku, origin),
                                "inbound": [{"order_id": value.event_id, "quantity": value.quantity,
                                             "expected_at": str(value.expected_at), "source_reference": value.source}
                                            for value in inbound],
                                "economic_profile": deepcopy(econ_row), "applied_rule_ids": applied}
            approval_constraints[item_id] = {
                "unit_quantum": result.diagnostics.get("unit_quantum", 1),
                "min_order_qty": result.diagnostics.get("min_order_qty_inventory_units"),
                "order_multiple": result.diagnostics.get("order_multiple_inventory_units"),
                "minimum_safe_quantity": max(result.diagnostics.get("service_floor_quantity", 0),
                                             result.diagnostics.get("hard_material_floor_quantity", 0)),
                "constraints_complete": result.diagnostics.get("min_order_qty_inventory_units") is not None
                                        and result.diagnostics.get("order_multiple_inventory_units") is not None
                                        and bool(profile and profile.get("warehouse_scope_verified")),
            }
        items = [value["item"] for value in details.values()]
        return {"response": {"meta": meta, "summary": summary(items), "items": items},
                "details": details, "request": request, "approval_constraints": approval_constraints}
