"""Production decision bridge: mock forecast/IO only; never mock recommend().

These are synthetic integration fixtures, not forecast-accuracy measurements.
No training or partner-workbook access is required.
"""
from copy import deepcopy
from datetime import date
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient

import numpy as np
import pandas as pd
from openpyxl import Workbook

from backend.app import ml_adapter
from backend.app.contracts import DomainError, validate
from backend.app.pipeline import calculate
from backend.app.main import create_app
from model.forecast_v2.data import Panel


class AdapterIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.normalized = self.root / "uploads" / "fixture-se" / "normalized"
        self.normalized.mkdir(parents=True)
        self.dataset = {
            "dataset_id": "fixture-se", "dataset_version": "fixture-v1",
            "data_as_of": "2026-09-22", "source_kind": "observed", "issues": [],
            "supplier_ids": ["systeme-electric"],
        }
        self.request = {
            "dataset_id": "fixture-se", "as_of_date": "2026-09-22",
            "warehouse_ids": ["almaty"], "category_codes": [], "horizon_days": 28,
            "lead_time_days": 7, "review_period_days": 21, "mode": "scenario",
            "category_policies": [], "growth_adjustments": [], "budget_kzt": None,
            "request_ai_review": False,
            "economic_profiles": [{"sku": "001", "underage_cost": 9, "overage_cost": 1,
                                   "unit_cost": 5, "currency": "KZT", "horizon_days": 28,
                                   "source_kind": "manual", "rationale": "Test scenario, not partner economics"}],
        }
        self.profiles = {"001": {
            "inventory_as_of": "2026-09-22", "on_hand": 100, "reserved": 0,
            "free_stock": 100, "source": "synthetic-test-fixture:row2",
            "category": None, "supplier_article": "ART001", "name": "Synthetic test product",
            "order_multiple": 10, "min_order_qty": None, "inbound_quantity": 0,
            "inbound_eta": "2026-09-24", "unit": "шт", "warehouse_scope_verified": True,
        }}
        dates = pd.date_range("2025-01-01", "2026-09-22")
        self.panel = Panel("SE__pieces", "SE", "шт", "Алматы",
                           pd.DataFrame(10.0, index=["001"], columns=dates), pd.DataFrame(), {})
        self.frame = pd.DataFrame({"sku": ["001"]})
        self.context = {}
        (self.normalized / "metadata.json").write_text(json.dumps({"model_input_root": "model_inputs"}))

    def tearDown(self):
        self.directory.cleanup()

    def run_adapter(self, via_http=False, **updates):
        req = deepcopy(self.request)
        req.update(updates)
        (self.normalized / "current.json").write_text(json.dumps(self.profiles, ensure_ascii=False), encoding="utf-8")
        (self.normalized / "context.json").write_text(json.dumps(self.context, ensure_ascii=False), encoding="utf-8")
        env = {"DATA_DIR": str(self.root), "TIREK_PIPELINE": "backend.app.ml_adapter:TirekCalculationPipeline"}
        forecast = (self.frame, np.asarray([280.0]),
                    {"selected": "mean364", "best_ml": "ridge_100", "trained_as_of": "2026-09-21"}, "a" * 64)
        # Retain the actual production core. Source readers and model execution
        # are replaced by a deterministic fixture to isolate bridge semantics.
        with patch.dict("os.environ", env), patch.object(ml_adapter, "load_panels", return_value=([self.panel], {})), \
                patch.object(ml_adapter, "_load_forecast", return_value=forecast), \
                patch.object(ml_adapter, "_daily_path", return_value=[10.0] * 28):
            if via_http:
                app = create_app(self.root / 'http.sqlite3')
                with TestClient(app) as client:
                    with app.state.store.transaction() as db:
                        app.state.store.put(db, 'dataset', self.dataset['dataset_id'],
                                            {**self.dataset, 'calculation_allowed': True})
                    created = client.post('/api/v1/calculations', json=req,
                                          headers={'Idempotency-Key': 'http-integration'})
                    self.assertEqual(created.status_code, 202, created.text)
                    job = client.get('/api/v1/jobs/' + created.json()['job_id']).json()
                    self.assertEqual(job['status'], 'succeeded', job)
                    prefix = '/api/v1/calculations/' + job['resource_id']
                    response = client.get(prefix + '/recommendations').json()
                    item = response['items'][0]
                    detail = client.get(prefix + '/items/' + item['item_id']).json()
                    rejected = client.post(prefix + '/approve', json={
                        'expected_revision': 1, 'selected_item_ids': [item['item_id']],
                        'acknowledged_issue_codes': [issue['code'] for issue in item['issues']],
                    }, headers={'Idempotency-Key': 'approve-incomplete'})
                    self.assertEqual(rejected.status_code, 422, rejected.text)
                    result = {'response': response, 'details': {item['item_id']: detail}}
            else:
                result = calculate(deepcopy(self.dataset), req, "calc-integration")
        validate("RecommendationsResponse", result["response"])
        for detail in result["details"].values():
            validate("ItemDetail", detail)
        return result

    def test_http_job_list_detail_and_utf8_sources(self):
        self.profiles['001']['name'] = 'Выключатель ИК — проверка кириллицы'
        result = self.run_adapter(via_http=True)
        self.assertEqual(self.item(result)['name'], self.profiles['001']['name'])
        self.assertEqual(self.item(result)['recommended_quantity'], 180)
        self.assertEqual(self.item(result)['forecast']['method'], 'baseline')

    def item(self, result):
        return result["response"]["items"][0]

    def test_real_decision_core_and_contract_provisional_party(self):
        result = self.run_adapter()
        item = self.item(result)
        self.assertEqual(item["recommended_quantity"], 180)
        self.assertIsNone(item["final_quantity"])
        self.assertIsNone(item["min_order_qty"])
        self.assertEqual(item["order_multiple"], 10)
        self.assertEqual(item["decision_status"], "needs_review")
        self.assertIsNone(item["forecast"]["p90"])
        self.assertIsNone(item["order_cost_kzt"])
        self.assertFalse(result["response"]["summary"]["order_cost_complete"])
        self.assertFalse(result["approval_constraints"][item["item_id"]]["constraints_complete"])

    def test_missing_current_stock_preserves_forecast_but_no_quantity(self):
        self.profiles = {}
        item = self.item(self.run_adapter())
        self.assertEqual(item["decision_status"], "needs_data")
        self.assertIsNone(item["recommended_quantity"])
        self.assertIsNone(item["final_quantity"])
        self.assertEqual(item["forecast"]["mean"], 280)

    def test_growth_and_dated_inbound_reach_core(self):
        self.profiles["001"].update(inbound_quantity=50, inbound_eta="2026-09-24")
        with_inbound = self.item(self.run_adapter())
        self.assertEqual(with_inbound["recommended_quantity"], 130)
        growth = [{"sku": "001", "rate": 1, "valid_from": "2026-09-23", "valid_to": "2026-10-20",
                   "source_kind": "manual", "rationale": "Explicit test doubling"}]
        grown = self.item(self.run_adapter(growth_adjustments=growth))
        self.assertGreater(grown["recommended_quantity"], with_inbound["recommended_quantity"])
        self.profiles["001"].update(on_hand=0, free_stock=0, inbound_quantity=280, inbound_eta="2026-10-20")
        late = self.item(self.run_adapter())
        self.assertEqual(late["urgency"], "critical")
        self.assertTrue(any(i["code"] == "SHORTAGE_BEFORE_NEW_ORDER_ARRIVAL" for i in late["issues"]))

    def test_no_fake_llm_review(self):
        item = self.item(self.run_adapter(request_ai_review=True))
        self.assertEqual(item["ai"]["status"], "unavailable")
        self.assertIsNone(item["ai"]["verdict"])
        self.assertIsNone(item["ai"]["provider_model"])

    def test_foreign_warehouse_material_is_not_added_to_almaty(self):
        baseline = self.item(self.run_adapter())["recommended_quantity"]
        self.context = {"material_requirements": [{
            "requirement_id": "astana-1", "sku": "001", "warehouse_id": "astana",
            "quantity": 100, "unit": "шт", "needed_at": "2026-09-30",
            "already_accounted_quantity": 0, "source_kind": "manual", "reference": "astana-fixture",
        }]}
        self.assertEqual(self.item(self.run_adapter())["recommended_quantity"], baseline)

    def test_baseline_is_not_labeled_as_ml(self):
        self.assertEqual(self.item(self.run_adapter())["forecast"]["method"], "baseline")

    def test_category_fallback_quantile_does_not_override_explicit_economics(self):
        self.profiles["001"]["category"] = "7"
        econ = deepcopy(self.request["economic_profiles"])
        econ[0].update(underage_cost=0, overage_cost=1)
        policy = [{"category_raw": "7", "target_quantile": .9, "minimum_target_quantile": None,
                   "source_kind": "manual", "rationale": "Fallback only, no economic floor"}]
        item = self.item(self.run_adapter(economic_profiles=econ, category_policies=policy))
        self.assertEqual(item["recommended_quantity"], 0)

    def test_matching_category_is_not_warned_as_unmapped(self):
        self.profiles["001"]["category"] = "7"
        policy = [{"category_raw": "7", "target_quantile": .9, "minimum_target_quantile": .8,
                   "source_kind": "manual", "rationale": "Confirmed category profile"}]
        item = self.item(self.run_adapter(category_policies=policy))
        self.assertFalse(any(i["code"] == "CATEGORY_POLICY_UNMAPPED" for i in item["issues"]))

    def test_confirmed_minimum_reaches_core_and_approval_constraints(self):
        self.profiles["001"]["min_order_qty"] = 250
        result = self.run_adapter()
        item = self.item(result)
        self.assertEqual(item["min_order_qty"], 250)
        self.assertEqual(item["recommended_quantity"], 250)
        self.assertEqual(item["final_quantity"], 250)
        self.assertTrue(result["approval_constraints"][item["item_id"]]["constraints_complete"])
        self.assertEqual(item["decision_status"], "needs_review")
        self.assertTrue(any(i["code"] == "UNCALIBRATED_SINGLE_FORECAST_PATH" for i in item["issues"]))

    def test_zero_minimum_is_explicit_and_distinct_from_unknown(self):
        self.profiles["001"]["min_order_qty"] = 0
        item = self.item(self.run_adapter())
        self.assertEqual(item["min_order_qty"], 0)
        self.assertEqual(item["final_quantity"], 180)
        self.assertEqual(item["order_cost_kzt"], 900)

    def test_conflicting_category_target_and_minimum_are_rejected(self):
        self.profiles["001"]["category"] = "7"
        policy = [{"category_raw": "7", "target_quantile": .2, "minimum_target_quantile": .9,
                   "source_kind": "manual", "rationale": "Contradiction"}]
        for economics in ([], self.request["economic_profiles"]):
            with self.subTest(economics=bool(economics)), self.assertRaises(DomainError) as caught:
                self.run_adapter(category_policies=policy, economic_profiles=economics)
            self.assertEqual(caught.exception.code, "INVALID_PARAMETERS")

    def test_duplicate_overlapping_and_reversed_growth_periods_rejected(self):
        growth = {"sku": "001", "rate": .1, "valid_from": "2026-09-23", "valid_to": "2026-09-30",
                  "source_kind": "manual", "rationale": "Approved plan"}
        cases = [[growth, growth],
                 [growth, growth | {"valid_from": "2026-09-30", "valid_to": "2026-10-05", "rate": .2}],
                 [growth | {"valid_from": "2026-10-01"}]]
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(DomainError) as caught:
                self.run_adapter(growth_adjustments=changes)
            self.assertEqual(caught.exception.code, "INVALID_PARAMETERS")
        result = self.run_adapter(growth_adjustments=[growth, growth | {"valid_from": "2026-10-01", "valid_to": "2026-10-20"}])
        self.assertEqual(self.item(result)["recommended_quantity"], 210)

    def test_unknown_and_partial_inbound_block_quantity_without_false_zero(self):
        for quantity, eta, missing in ((None, "2026-09-24", "INBOUND_QUANTITY"),
                                       (10, None, "INBOUND_ETA"), (None, None, "INBOUND_QUANTITY")):
            with self.subTest(quantity=quantity, eta=eta):
                self.profiles["001"].update(min_order_qty=0, inbound_quantity=quantity, inbound_eta=eta)
                item = self.item(self.run_adapter())
                self.assertEqual(item["decision_status"], "needs_data")
                self.assertIsNone(item["inbound_within_horizon"])
                self.assertEqual(item["recommended_quantity"], 180)
                self.assertIsNone(item["final_quantity"])
                self.assertTrue(any(i["code"] == "REQUIRED_" + missing for i in item["issues"]))
                self.assertTrue(any(i["code"] == "PROVISIONAL_QUANTITY" for i in item["issues"]))

    def test_explicit_zero_inbound_does_not_require_eta(self):
        for eta in (None, "2026-01-01"):
            with self.subTest(eta=eta):
                self.profiles["001"].update(min_order_qty=0, inbound_quantity=0, inbound_eta=eta)
                item = self.item(self.run_adapter())
                self.assertEqual(item["inbound_within_horizon"], 0)
                self.assertEqual(item["final_quantity"], 180)
                self.assertFalse(any("INBOUND_ETA" in i["code"] or "REFRESHED_ETA" in i["code"] for i in item["issues"]))

    def test_current_and_material_only_skus_remain_visible_without_fake_forecast(self):
        self.profiles["NEW-STOCK"] = deepcopy(self.profiles["001"])
        self.profiles["NEW-STOCK"].update(unit=None)
        self.context = {"material_requirements": [{
            "requirement_id": "new1", "sku": "NEW-MATERIAL", "warehouse_id": "almaty",
            "quantity": 100, "unit": "шт", "needed_at": "2026-09-30",
            "already_accounted_quantity": 0, "source_kind": "manual", "reference": "New confirmed need",
        }]}
        result = self.run_adapter()
        items = {item["sku"]: item for item in result["response"]["items"]}
        self.assertEqual(set(items), {"001", "NEW-STOCK", "NEW-MATERIAL"})
        for sku in ("NEW-STOCK", "NEW-MATERIAL"):
            item = items[sku]
            self.assertEqual(item["decision_status"], "needs_data")
            self.assertIsNone(item["forecast"])
            self.assertIsNone(item["recommended_quantity"])
            self.assertIsNone(item["final_quantity"])
            self.assertTrue(any(i["code"] == "REQUIRED_FORECAST_HISTORY" for i in item["issues"]))
            self.assertEqual(result["details"][item["item_id"]]["history"], [])
        self.assertEqual(items["NEW-STOCK"]["unit"], "unknown")
        self.assertEqual(items["NEW-MATERIAL"]["unit"], "шт")
        self.assertEqual(items["NEW-MATERIAL"]["material_requirement_uncovered"], 100)

    def test_missing_history_rows_respect_category_and_warehouse_scope(self):
        self.profiles["NEW"] = deepcopy(self.profiles["001"])
        self.profiles["NEW"]["category"] = "new"
        scoped = self.run_adapter(category_codes=["new"])
        self.assertEqual([i["sku"] for i in scoped["response"]["items"]], ["NEW"])
        self.assertEqual(self.run_adapter(warehouse_ids=["astana"])["response"]["items"], [])

    def test_unverified_warehouse_remains_blocked_even_with_known_supplier_constraints(self):
        self.profiles["001"].update(min_order_qty=0, warehouse_scope_verified=False)
        result = self.run_adapter()
        item = self.item(result)
        self.assertIsNone(item["final_quantity"])
        self.assertEqual(item["recommended_quantity"], 180)
        self.assertTrue(any(i["code"] == "REQUIRED_WAREHOUSE_SCOPE_CONFIRMATION" for i in item["issues"]))
        self.assertFalse(result["approval_constraints"][item["item_id"]]["constraints_complete"])

    def test_no_quantiles_are_manufactured_from_aggregate_error_reports(self):
        self.profiles["001"]["min_order_qty"] = 0
        item = self.item(self.run_adapter())
        forecast = item["forecast"]
        for field in ("p10", "p50", "p90", "target_stock"):
            self.assertIsNone(forecast[field])
        self.assertEqual(forecast["calibration_status"], "insufficient_data")
        self.assertIn("одна дневная траектория", forecast["note"])
        self.assertEqual(item["decision_status"], "needs_review")

    def test_normalizer_counts_actual_seasonality_year_rows_and_describes_both_negative_policies(self):
        # Minimal synthetic source audit; no partner workbook is read.
        audit = {
            "transactions": {"data_as_of": "2026-09-22", "used_rows": 1, "sku_count": 1, "negative_rows": 2},
            "monthly_sales": {"sku_count": 1, "negative_cells_excluded": 4},
            "monthly_stock": {"sku_count": 1}, "current_profiles": 1, "moq_profiles": 1,
            "current_snapshot": {"as_of": "2026-09-22", "unit_counts": {"шт": 1}, "missing_multiple": 0},
            "coverage": {"current_profile": {"daily_skus_missing": 0, "additional_skus": 0}},
            "daily_monthly_reconciliation": {"differing_sku_months": 0, "compared_complete_sku_months": 1},
        }
        turnover = {pd.Timestamp("2023-01-01"): 1, pd.Timestamp("2023-02-01"): 2,
                    pd.Timestamp("2024-01-01"): 3}
        sources = {"audit": audit, "current": self.profiles, "turnover": turnover}
        report = {**self.dataset, "sources": [{"role": role} for role in ml_adapter.ROLE_NAMES]}
        with patch.object(ml_adapter, "_prepare_model_inputs", return_value=self.root / "model_inputs"), \
                patch.object(ml_adapter, "load_se", return_value=sources):
            output = ml_adapter.normalize_dataset(self.root, report, {})
        seasonality = next(source for source in output["sources"] if source["role"] == "seasonality")
        self.assertEqual(seasonality["rows_used"], 2)
        message = next(issue["message"] for issue in output["issues"] if issue["code"] == "NEGATIVE_SALES_EXCLUDED")
        self.assertIn("Нормализатор исключил 2", message)
        self.assertIn("4 отрицательных месячных", message)
        self.assertIn("frozen forecast v2", message)
        self.assertIn("ограничивает их вклад нулём", message)

    def test_stockout_context_uses_exposure_aware_regular_forecast(self):
        dates = self.panel.daily.columns
        self.panel.daily.loc["001", dates[-28:]] = 0
        context = {"stockout_intervals": [{
            "sku": "001", "warehouse_id": "almaty",
            "start_date": str(dates[-28].date()), "end_date": str(dates[-2].date()),
            "source_kind": "manual", "reference": "availability-ledger",
        }]}
        forecasts, audits = ml_adapter._context_forecasts(
            self.panel, dates[-1], context, None)
        self.assertGreater(sum(forecasts["001"]), 250)
        self.assertGreaterEqual(audits["001"]["zero_exposure_days"], 27)

    def test_client_labels_remove_oneoff_window_and_require_event_mapping(self):
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["Дата", "Номер", "Документ", "Код", "Номенклатура", "Ед.", "Склад", "Количество"])
        project_day = pd.Timestamp("2026-08-01")
        sheet.append([str(project_day), "PROJECT-1", "Расходная накладная PROJECT-1", "001",
                      "Synthetic test product", "шт", "Алматы", 500])
        path = self.root / "transactions.xlsx"
        workbook.save(path)
        self.panel.daily.loc["001", project_day] += 500
        context = {"client_labels": [{"source_event_id": "PROJECT-1",
                                      "pseudonymous_client_id": "client-hash"}]}
        forecasts, audits = ml_adapter._context_forecasts(
            self.panel, self.panel.daily.columns[-1], context, path)
        self.assertLess(sum(forecasts["001"]), 350)
        self.assertEqual(sum(row["quantity"] for row in audits["001"]["excluded_client_windows"]), 500)
        context["client_labels"][0]["source_event_id"] = "UNKNOWN"
        with self.assertRaises(DomainError):
            ml_adapter._context_forecasts(self.panel, self.panel.daily.columns[-1], context, path)


if __name__ == "__main__":
    unittest.main()


def test_frozen_weights_load_and_predict_on_synthetic_history():
    """Exercise real hashes/features/weights; this is not an accuracy measurement."""
    daily = pd.DataFrame(10.0, index=['SMOKE-ONLY'],
                         columns=pd.date_range('2025-01-01', '2026-09-22'))
    monthly = pd.DataFrame(300.0, index=['SMOKE-ONLY'],
                           columns=pd.date_range('2024-01-01', periods=12, freq='MS'))
    panel = Panel('SE__pieces', 'SE', 'шт', 'Алматы', daily, monthly, {})
    frame, values, entry, selection_hash = ml_adapter._load_forecast(panel, pd.Timestamp('2026-09-22'))
    assert len(frame) == len(values) == 1
    assert np.isfinite(values).all() and (values >= 0).all()
    assert len(selection_hash) == 64
    assert entry['trained_as_of'] <= '2026-09-22'
