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

import numpy as np
import pandas as pd

from backend.app import ml_adapter
from backend.app.contracts import validate
from backend.app.pipeline import calculate
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

    def run_adapter(self, **updates):
        req = deepcopy(self.request)
        req.update(updates)
        (self.normalized / "current.json").write_text(json.dumps(self.profiles))
        (self.normalized / "context.json").write_text(json.dumps(self.context))
        env = {"DATA_DIR": str(self.root), "TIREK_PIPELINE": "backend.app.ml_adapter:TirekCalculationPipeline"}
        forecast = (self.frame, np.asarray([280.0]),
                    {"selected": "mean364", "best_ml": "ridge_100", "trained_as_of": "2026-09-21"}, "a" * 64)
        # Retain the actual production core. Source readers and model execution
        # are replaced by a deterministic fixture to isolate bridge semantics.
        with patch.dict("os.environ", env), patch.object(ml_adapter, "load_panels", return_value=([self.panel], {})), \
                patch.object(ml_adapter, "_load_forecast", return_value=forecast), \
                patch.object(ml_adapter, "_daily_path", return_value=[10.0] * 28):
            result = calculate(deepcopy(self.dataset), req, "calc-integration")
        validate("RecommendationsResponse", result["response"])
        for detail in result["details"].values():
            validate("ItemDetail", detail)
        return result

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


if __name__ == "__main__":
    unittest.main()
