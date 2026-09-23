"""Normative business regressions from review-2; no forecast accuracy claims.

Expected values are hand arithmetic, exact decimal input algebra, or the reviewed
fixtures. Neither reference output nor its helpers generate expected quantities.
"""
from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal
import json
from pathlib import Path
import unittest

from model.testbed.business import check_cases, reference_calculate, validate_response

FIXTURES = json.loads(Path(__file__).parents[1].joinpath('fixtures/business_cases.json').read_text())['cases']
CASES = {case['id']: case for case in FIXTURES}


def request(case='stock_base', **changes):
    value = deepcopy(CASES[case]['input'])
    value['items'][0].update(changes)
    return value


class BusinessRepairTests(unittest.TestCase):
    def test_reviewed_fixtures_and_all_33_boundary_regressions(self):
        results = check_cases(reference_calculate)
        self.assertEqual(33, sum(c['id'].startswith('regression_') for c in FIXTURES))
        self.assertEqual([], [(r['id'], r['failures']) for r in results if not r['passed']])

    def test_decimal_exact_batches_20_independent_boundaries(self):
        for tenths in range(1, 21):
            with self.subTest(tenths=tenths):
                needed = Decimal(tenths) / Decimal(10)
                req = request(unit='м', order_unit='м', unit_quantum=0.01,
                              on_hand=float(Decimal(280) - needed), min_order_qty=0.1, order_multiple=0.1)
                actual = reference_calculate(req)
                self.assertEqual(needed, Decimal(str(actual['items'][0]['quantity'])))
                self.assertEqual([], validate_response(req, actual))

    def test_money_48_equal_budget_boundaries(self):
        for quantity in range(1, 13):
            for cents in (1, 3, 7, 10):
                with self.subTest(quantity=quantity, cents=cents):
                    price = Decimal(cents) / Decimal(100)
                    budget = Decimal(quantity) * price
                    req = request(on_hand=280 - quantity, unit_cost=float(price))
                    req['budget_kzt'] = float(budget)
                    actual = reference_calculate(req)
                    self.assertEqual(budget, Decimal(str(actual['total_cost_kzt'])))
                    self.assertEqual(0, actual['budget_excess_kzt'])
                    self.assertFalse(actual['approval_blocked'])
                    self.assertEqual([], actual['rules'])
                    self.assertEqual([], validate_response(req, actual))

    def test_late_receipt_requires_200_and_independent_path_has_no_unmet_demand(self):
        req = request('inbound_last_day')
        response = reference_calculate(req)
        item = response['items'][0]
        self.assertEqual(200, item['quantity'])  # 27*10 -70; old receipt arrives day28.
        self.assertEqual(110, item['aggregate_need'])
        self.assertEqual(200, item['temporal_lower_bound'])
        self.assertIn('SUPPLY-02', item['rules'])
        stock = Decimal(70)
        unmet = Decimal(0)
        for day in range(1, 29):
            incoming = Decimal(200 if day == 7 else 100 if day == 28 else 0)
            unmet += max(Decimal(0), Decimal(10) - stock - incoming)
            stock = max(Decimal(0), stock + incoming - Decimal(10))
            if day == 27:
                self.assertEqual(0, stock)
        self.assertEqual(0, unmet)
        self.assertEqual(90, stock)
        self.assertEqual(0, item['planning_unmet_with_order'])
        self.assertEqual(0, item['mean_unmet_with_order'])
        self.assertEqual(8, item['first_deficit_day'])  # Explicitly WITHOUT normal order.

    def test_economic_target_not_overridden_by_mean(self):
        expensive = reference_calculate(request('economics_expensive_low_contribution'))['items'][0]
        self.assertEqual((0.1, 140, 70), (expensive['target_quantile'], expensive['target_stock'], expensive['quantity']))
        self.assertEqual('horizon_target_proportional_to_daily_mean', expensive['planning_profile'])
        self.assertEqual(0, expensive['planning_unmet_with_order'])
        self.assertEqual(140, expensive['mean_unmet_with_order'])
        self.assertTrue(expensive['mean_risk_after_order'])
        self.assertFalse(expensive['requires_expedite'])  # Does not mean no later mean-risk.
        self.assertEqual(15, expensive['mean_first_unmet_day_with_order'])
        rare = reference_calculate(request('economics_rare_high_margin'))['items'][0]
        self.assertEqual((0, 0), (rare['target_stock'], rare['quantity']))
        self.assertEqual(28, rare['mean_unmet_with_order'])

    def test_early_gap_survives_normal_order_and_zero_lead_is_start_day_one(self):
        early = reference_calculate(request('ordinary_order_does_not_erase_gap'))['items'][0]
        self.assertEqual(280, early['quantity'])
        self.assertEqual(60, early['early_shortfall'])
        self.assertEqual(60, early['mean_unmet_with_order'])
        self.assertEqual(60, early['planning_unmet_with_order'])
        self.assertTrue(early['requires_expedite'])
        zero = reference_calculate(request(lead_time_days=0, review_period_days=28))['items'][0]
        self.assertEqual(210, zero['quantity'])
        self.assertEqual(0, zero['mean_unmet_with_order'])
        self.assertFalse(zero['requires_expedite'])

    def test_four_net_gross_equivalences(self):
        for reserve in (0, 20, 50, 200):
            gross = reference_calculate(request(on_hand=50 + reserve, reserved=reserve))['items'][0]
            net = reference_calculate(request(on_hand=999, reserved=reserve, free_stock=50))['items'][0]
            self.assertEqual((230, 50), (gross['quantity'], gross['free_stock']))
            self.assertEqual((230, 50), (net['quantity'], net['free_stock']))

    def test_all_six_checker_mutants_rejected_in_every_changed_case(self):
        mutations = {
            'status': lambda r: r.update(status='needs_data') if r['status'] == 'ok' else None,
            'rules': lambda r: r.update(rules=' '.join(r['rules'])),
            'supplier': lambda r: r.update(supplier_id='wrong-supplier'),
            'reason': lambda r: r.update(reason=True),
            'quantity': lambda r: r.update(quantity=-1) if r['quantity'] is not None else None,
            'unit': lambda r: r.update(unit='wrong-unit') if r['quantity'] is not None else None,
        }
        for name, mutate in mutations.items():
            with self.subTest(mutation=name):
                changed = set()
                def adapter(req):
                    result = reference_calculate(req)
                    before = deepcopy(result)
                    for row in result['items']:
                        mutate(row)
                    if result != before:
                        changed.add(json.dumps(req, sort_keys=True))
                    return result
                results = check_cases(adapter)
                changed_ids = {c['id'] for c in FIXTURES if json.dumps(c['input'], sort_keys=True) in changed}
                self.assertTrue(changed_ids)
                self.assertEqual([], [r['id'] for r in results if r['id'] in changed_ids and r['passed']])

    def test_invalid_numeric_domains_bool_nan_infinity_and_missing_are_blocked(self):
        patches = []
        for bad in (-1, True, float('nan'), float('inf'), float('-inf')):
            for field in ('on_hand', 'reserved', 'free_stock', 'unit_cost', 'unit_quantum', 'min_order_qty', 'order_multiple'):
                patches.append({field: bad})
            patches.append({'daily_mean': [bad] + [10] * 27})
            patches.append({'inbound': [{'quantity': bad, 'status': 'confirmed', 'expected_at': '2026-04-05'}]})
        patches += [{'lead_time_days': -1, 'review_period_days': 29}, {'lead_time_days': 29, 'review_period_days': -1},
                    {'lead_time_days': 7.0}, {'review_period_days': True},
                    {'material_requirements': [{'quantity': 50, 'already_accounted_quantity': 51, 'needed_at': '2026-04-05'}]},
                    {'stock_current': None}, {'daily_mean': [None] + [10] * 27}]
        for patch in patches:
            with self.subTest(patch=patch):
                result = reference_calculate(request(**patch))
                self.assertEqual('needs_data', result['items'][0]['status'])
                self.assertIsNone(result['items'][0]['quantity'])
                self.assertTrue(result['approval_blocked'])
                json.dumps(result, allow_nan=False)

    def test_dates_are_canonical_and_valid_before_any_totals(self):
        for stamp in ('2026-04-05x', '20260405', '2026-W14-7', '2026-02-30', '2026-04-05T00:00:00', None, 20260405):
            for changes in ({'inbound': [{'quantity': 100, 'status': 'confirmed', 'expected_at': stamp}]},
                            {'material_requirements': [{'quantity': 100, 'already_accounted_quantity': 0, 'needed_at': stamp}]}):
                with self.subTest(stamp=stamp, changes=changes):
                    actual = reference_calculate(request(**changes))
                    self.assertEqual('needs_data', actual['items'][0]['status'])
                    self.assertIsNone(actual['items'][0]['quantity'])
        for stamp in ('2026-04-01x', '9999-12-31'):
            req = request()
            req['as_of'] = stamp
            self.assertEqual('needs_data', reference_calculate(req)['items'][0]['status'])

    def test_probability_mass_quantile_endpoints_and_zero_probability_support(self):
        economics = {'underage_cost': 1, 'overage_cost': 0, 'horizon_days': 28, 'source': 'audit'}
        for mass in (0.9999999999, 1, 1.0000000001):
            req = request(economics=economics, horizon_distribution=[{'quantity': 280, 'probability': mass}, {'quantity': 999, 'probability': 0}])
            row = reference_calculate(req)['items'][0]
            self.assertEqual(('ok', 1, 280, 210), (row['status'], row['target_quantile'], row['target_stock'], row['quantity']))
        for mass in (0, -1, 0.9, 1.1, True, float('nan'), float('inf')):
            row = reference_calculate(request(economics=economics, horizon_distribution=[{'quantity': 280, 'probability': mass}]))['items'][0]
            self.assertEqual('needs_data', row['status'])
        economics = dict(economics, underage_cost=0, overage_cost=1)
        row = reference_calculate(request(economics=economics))['items'][0]
        self.assertEqual((0, 0, 0), (row['target_quantile'], row['target_stock'], row['quantity']))

    def test_growth_and_category_minimum_follow_declared_openapi_boundaries(self):
        growth = {'id': 'decline', 'rate': -1, 'valid_from': '2026-04-02',
                  'valid_to': '2026-04-29', 'source': 'reviewed-decline'}
        bad = reference_calculate(request(growth_adjustments=[growth]))['items'][0]
        self.assertEqual('needs_data', bad['status'])
        self.assertIn('growth_adjustments.rate', bad['missing'])
        growth['rate'] = -0.5
        valid = reference_calculate(request(growth_adjustments=[growth]))['items'][0]
        self.assertEqual((140, 140, 70), (valid['forecast_mean'], valid['target_stock'], valid['quantity']))
        for floor in (0, -0.1, 1, 0.9, True, float('nan')):
            with self.subTest(floor=floor):
                req = request()
                req['category_policies']['A']['minimum_target_quantile'] = floor
                row = reference_calculate(req)['items'][0]
                self.assertEqual('needs_data', row['status'])
                self.assertIn('valid_category_policy', row['missing'])
        for floor in (None, 0.1, 0.8):
            req = request()
            req['category_policies']['A']['minimum_target_quantile'] = floor
            self.assertEqual('ok', reference_calculate(req)['items'][0]['status'])

    def test_partial_supplier_constraints_and_price_per_base_unit(self):
        for patch, expected in (({'on_hand': 69, 'min_order_qty': None, 'order_multiple': 5}, 215),
                                ({'on_hand': 279, 'min_order_qty': 10, 'order_multiple': None}, 10),
                                ({'on_hand': 255, 'order_unit': 'box', 'order_to_base_factor': 10,
                                  'min_order_qty': None, 'order_multiple': 2}, 40)):
            response = reference_calculate(request(**patch))
            self.assertEqual(expected, response['items'][0]['quantity'])
            self.assertEqual('needs_review', response['items'][0]['status'])
            self.assertTrue(response['approval_blocked'])
        response = reference_calculate(request('unit_boxes_to_pieces', unit_cost=0.1))
        self.assertEqual(40, response['items'][0]['quantity'])
        self.assertEqual(4, response['total_cost_kzt'])  # 40 pieces, not four boxes *0.1.

    def test_provenance_and_explanation_use_actual_evidence(self):
        a = reference_calculate(request())['items'][0]
        b = reference_calculate(request(on_hand=100))['items'][0]
        self.assertNotEqual(a['reason'], b['reason'])
        self.assertIn('free stock 70', a['reason'])
        self.assertIn('order 180', b['reason'])
        economics = {'underage_cost': 1, 'overage_cost': 1, 'horizon_days': 28, 'source': 'approved-profile-7'}
        c = reference_calculate(request(economics=economics))['items'][0]
        self.assertEqual('approved-profile-7', c['economics_source'])
        self.assertEqual('approved-profile-7', c['evidence']['POLICY-01']['economics_source'])
        self.assertTrue(all(c['evidence'][rule] for rule in c['rules']))

    def test_schema_rejects_absent_fields_bad_totals_and_hidden_nonfinite(self):
        req = request('missing_stock')
        good = reference_calculate(req)
        for key in ('total_cost_kzt', 'budget_excess_kzt', 'approval_blocked'):
            result = deepcopy(good)
            del result[key]
            self.assertTrue(validate_response(req, result))
        for key in ('quantity', 'missing', 'reason', 'unit', 'evidence'):
            result = deepcopy(good)
            del result['items'][0][key]
            self.assertTrue(validate_response(req, result))
        req = request()
        result = reference_calculate(req)
        result['known_cost_kzt'] = 1
        self.assertTrue(validate_response(req, result))
        result = reference_calculate(req)
        result['items'][0]['evidence']['bad'] = {'value': float('nan')}
        self.assertTrue(validate_response(req, result))

    def test_checker_input_mutation_does_not_rewrite_identity_oracle(self):
        def hostile(req):
            req['items'][0]['supplier_id'] = 'rewritten'
            return reference_calculate(req)
        self.assertTrue(all(not row['passed'] for row in check_cases(hostile)))

    def test_invalid_identities_remain_needs_data_without_invalid_group_keys(self):
        for field in ('supplier_id', 'unit', 'order_unit', 'category_raw'):
            for invalid in (None, [], {}, '', ' ', 42, True):
                with self.subTest(field=field, invalid=invalid):
                    req = request(**{field: invalid})
                    result = reference_calculate(req)
                    self.assertEqual('needs_data', result['items'][0]['status'])
                    self.assertIsNone(result['items'][0]['quantity'])
                    self.assertTrue(result['approval_blocked'])
                    self.assertIn(field, result['items'][0]['missing'])
                    self.assertEqual([], validate_response(req, result))
                    json.dumps(result, allow_nan=False)
                    if field == 'supplier_id':
                        self.assertEqual({}, result['supplier_groups'])
        req = request('two_suppliers_budget_total', supplier_id=None)
        result = reference_calculate(req)
        self.assertEqual({'supplier-B': ['0002']}, result['supplier_groups'])
        self.assertEqual([], validate_response(req, result))
        for invalid in (None, [], {}):
            with self.assertRaises(ValueError):
                reference_calculate(request(sku=invalid))

    def test_constraint_numeric_range_is_explicit_not_decimal_context_dependent(self):
        for patch in ({'order_multiple': 1e30, 'unit_quantum': 0.01},
                      {'unit_quantum': 1e-30}, {'order_multiple': 1e-30},
                      {'order_unit': 'box', 'order_to_base_factor': 1e30},
                      {'min_order_qty': 1e30}):
            with self.subTest(patch=patch):
                req = request(**patch)
                result = reference_calculate(req)
                self.assertEqual('needs_data', result['items'][0]['status'])
                self.assertIsNone(result['items'][0]['quantity'])
                self.assertIn('supported_order_constraints_range', result['items'][0]['missing'])
                self.assertEqual([], validate_response(req, result))
                json.dumps(result, allow_nan=False)
        for quantum in (1e-12, 1e12):
            result = reference_calculate(request(unit_quantum=quantum, order_multiple=quantum))
            self.assertEqual('ok', result['items'][0]['status'])

    def test_repeating_planning_ratios_do_not_add_an_extra_batch(self):
        for stock, expected in ((0.2, 0.8), (0.3, 0.7)):
            with self.subTest(stock=stock):
                req = request(on_hand=stock, reserved=0, lead_time_days=0,
                              review_period_days=28, daily_mean=[1] * 27 + [8],
                              horizon_distribution=[{'quantity': 1, 'probability': 1}],
                              unit='м', order_unit='м', unit_quantum=0.1,
                              min_order_qty=0, order_multiple=0.1)
                result = reference_calculate(req)
                row = result['items'][0]
                # Exact target1 minus stock. Daily 1/35 allocation may repeat,
                # but no receipt timing changes this horizon lower bound.
                self.assertEqual(expected, row['quantity'])
                self.assertEqual(expected, row['aggregate_need'])
                self.assertEqual(expected, row['temporal_lower_bound'])
                self.assertEqual(0, row['planning_unmet_with_order'])
                self.assertEqual([], validate_response(req, result))


if __name__ == '__main__':
    unittest.main()
