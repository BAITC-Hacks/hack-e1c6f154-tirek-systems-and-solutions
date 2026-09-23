#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname -- "$0")/../../../../.."
PYTHONDONTWRITEBYTECODE=1 "${PYTHON:-python3}" - <<'PY'
"""Audit the frozen calculator; every oracle is manual or Decimal arithmetic.

No forecast score is inferred from these procurement checks. A detected defect is
an audit result, not a runner failure. Exceptions in the runner itself are fatal.
"""
from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal, ROUND_CEILING
import hashlib
import json
from pathlib import Path
import platform
import subprocess

from model.testbed.business import check_cases, reference_calculate

ROOT = Path('model/testbed')
DEST = ROOT / 'audits/review-2/business'
CASES = json.loads((ROOT / 'fixtures/business_cases.json').read_text())['cases']
BY_ID = {c['id']: c for c in CASES}
BASE = BY_ID['stock_base']['input']
D = lambda v: Decimal(str(v))
rows = []


def equal(expected, actual):
    if isinstance(expected, bool) or isinstance(actual, bool):
        return type(expected) is type(actual) and expected == actual
    if isinstance(expected, (int, float, Decimal)) and isinstance(actual, (int, float, Decimal)):
        return D(actual).is_finite() and abs(D(expected) - D(actual)) <= D('0.00000001')
    if isinstance(expected, dict):
        return isinstance(actual, dict) and set(expected) == set(actual) and all(equal(v, actual[k]) for k, v in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and len(expected) == len(actual) and all(equal(a, b) for a, b in zip(expected, actual))
    return type(expected) is type(actual) and expected == actual


def selected(result, keys):
    answer = {}
    for key in keys:
        value = result
        try:
            for part in key.split('.'):
                value = value[int(part)] if isinstance(value, list) else value[part]
        except (KeyError, IndexError, TypeError, ValueError):
            value = '<missing>'
        answer[key] = value
    return answer


def record(name, group, expected, actual, rationale, severity='info', finding=None,
           classification='control', request=None, extra=None):
    row = {'id': name, 'group': group, 'classification': classification, 'severity': severity,
           'finding_id': finding, 'expected': expected, 'actual': actual,
           'passed': equal(expected, actual), 'rationale': rationale}
    if request is not None:
        row['input'] = request
    if extra:
        row.update(extra)
    rows.append(row)
    return row


def probe(name, item_changes, expected, rationale, severity='info', finding=None,
          classification='control', request_changes=None, case='stock_base', delete_fields=()):
    request = deepcopy(BY_ID[case]['input'])
    request['items'][0].update(deepcopy(item_changes))
    if request_changes:
        request.update(deepcopy(request_changes))
    for key in delete_fields:
        del request['items'][0][key]
    try:
        response = reference_calculate(deepcopy(request))
        actual = selected(response, expected)
    except Exception as exc:
        response = {'exception': type(exc).__name__, 'message': str(exc)}
        actual = response
    return record(name, 'independent_boundary_probes', expected, actual, rationale,
                  severity, finding, classification, request, {'full_response': response})


# Manual arithmetic ledger: quantity and status only. It intentionally does not
# read fixture expected answers, and is not a claim to independently validate all
# response fields. Requirement order and identities are checked separately below.
LEDGER = {
    'stock_base': (210, '280 - 70'),
    'stock_increased': (180, '280 - 100'),
    'stock_surplus_no_moq_order': (0, 'max(0, 280 - 500); no positive need'),
    'reserve_gross': (230, '280 - (70 - 20)'),
    'reserve_net_not_twice': (230, '280 - 50; free_stock is net'),
    'inbound_timely': (110, '280 - 70 - 100'),
    'inbound_late_gap': (150, '280 - 30 - 100; quantity alone cannot establish timing'),
    'inbound_before_gap': (150, '280 - 30 - 100'),
    'ordinary_order_does_not_erase_gap': (280, '280 - 0; early gap still exists'),
    'inbound_outside_horizon': (210, '280 - 70; exclude receipt after day28'),
    'inbound_cancelled': (210, '280 - 70; exclude cancelled receipt'),
    'inbound_last_day': (110, '280 - 70 - 100; horizon total, NOT feasible trajectory'),
    'moq_and_multiple': (15, 'ceil(max(280 - 275, 12) / 5) * 5'),
    'multiple_exact': (210, '(280 - 70) is an exact multiple of7'),
    'moq_unknown': (210, 'provisional base need280 - 70, needs_review'),
    'multiple_unknown': (210, 'provisional base need280 - 70, needs_review'),
    'category_A': (210, 'CDF at280 is0.8; target280 - stock70'),
    'category_B': (350, 'CDF at280 is0.8 < 0.9; target420 - stock70'),
    'external_growth': (266, '28 * 12 - 70'),
    'growth_partial_period': (238, '14 * 12 + 14 * 10 - 70'),
    'growth_outside_horizon': (210, '28 * 10 - 70; no overlapping growth dates'),
    'growth_already_in_forecast': (266, '336 - 70; coefficient already included'),
    'growth_duplicate_id': (266, '280 * 1.2 - 70; same adjustment only once'),
    'material_only_uncovered': (260, '280 + (50 - 20) - (70 - 20)'),
    'material_fully_reserved': (230, '280 + (20 - 20) - (70 - 20)'),
    'material_after_horizon': (210, '280 - 70; excluded later commitment'),
    'material_due_before_supply': (310, '280 + 100 - 70'),
    'economics_expensive_low_contribution': (70, 'q=10/100; target140 - stock70'),
    'economics_cheap_high_contribution': (350, 'q=90/100; target420 - stock70'),
    'economics_category_floor': (350, 'q=max(0.1,0.9); target420 - stock70'),
    'economics_rare_high_margin': (0, 'P(D=0)=0.95 >= q0.9; zero target'),
    'economics_frequent_low_unit_margin': (280, 'q0.5; deterministic demand280; zero stock'),
    'economics_project_only_obligation': (7, 'regular target0 + uncovered obligation7'),
    'budget_equal': (210, '280 - 70; cost21000 equals budget'),
    'budget_exceeded': (210, '280 - 70; cost21000 exceeds budget20000'),
    'budget_unknown_price': (210, '280 - 70; quantity known, cost unknown'),
    'unknown_price_without_budget': (210, '280 - 70; no invented price'),
    'missing_stock': (None, 'no usable stock'),
    'stale_stock': (None, 'stock explicitly stale'),
    'missing_reserve': (None, 'gross stock needs reserve quantity'),
    'missing_lead': (None, 'lead time unavailable'),
    'missing_policy': (None, 'neither policy nor economic profile'),
    'invalid_horizon': (None, '7 + 20 != 28'),
    'missing_demand': (None, 'daily demand missing'),
    'economics_wrong_horizon': (None, 'economics for7 days cannot set28-day policy'),
    'missing_conversion': (None, 'order/base conversion absent'),
    'invalid_multiple': (None, 'multiple0 invalid'),
    'unit_boxes_to_pieces': (40, 'need25; MOQ20 and multiple20 pieces =>40'),
    'unit_fractional_meters': (0.3, '(280 - 279.74) rounded to0.1 =>0.3'),
    'unit_missing_quantum': (None, 'quantum absent'),
    'unit_incompatible_multiple': (None, 'half-piece multiple versus whole-piece quantum'),
    'two_suppliers_budget_total': ([210, 210], 'each280 - 70; cost21000 + 42000'),
    'material_unit_mismatch': (None, 'cannot add boxes to pieces without conversion'),
    'inbound_unit_mismatch': (None, 'cannot add boxes to pieces without conversion'),
}
assert set(LEDGER) == set(BY_ID), 'Fixture inventory changed; manually review the ledger.'
original = check_cases(reference_calculate)
for case in CASES:
    quantity, proof = LEDGER[case['id']]
    quantity = quantity if isinstance(quantity, list) else [quantity]
    expected = [{'quantity': v, 'status': 'needs_data' if v is None else
                 'needs_review' if case['id'] in {'moq_unknown', 'multiple_unknown'} else 'ok'} for v in quantity]
    actual = reference_calculate(deepcopy(case['input']))
    record(case['id'], 'manual_fixture_quantity_status', expected,
           [{k: item.get(k) for k in ('quantity', 'status')} for item in actual['items']], proof)

# Positive boundaries: reserve, dates, coverage, units, individual economics.
probe('net_stock_without_gross_reserve', {'on_hand': None, 'reserved': None, 'free_stock': 50},
      {'items.0.free_stock': 50, 'items.0.quantity': 230, 'items.0.status': 'ok'}, 'Net50 needs280 - 50 =230; no second reserve subtraction.')
probe('net_stock_overrides_conflicting_gross', {'on_hand': 999, 'reserved': 800, 'free_stock': 50},
      {'items.0.free_stock': 50, 'items.0.quantity': 230}, 'Explicit net stock is authoritative under the documented contract.')
probe('overdue_commitment', {'material_requirements': [{'quantity': 80, 'already_accounted_quantity': 0, 'needed_at': '2026-03-31'}]},
      {'items.0.quantity': 290, 'items.0.first_deficit_day': 1, 'items.0.early_shortfall': 70, 'items.0.requires_expedite': True},
      'Overdue80 belongs on day1; quantity280+80-70=290; balance at day6 is70-80-60=-70.')
probe('partial_commitment_not_double_reserved', {'on_hand': 70, 'reserved': 20, 'material_requirements': [{'quantity': 50, 'already_accounted_quantity': 20, 'needed_at': '2026-04-10'}]},
      {'items.0.free_stock': 50, 'items.0.material_uncovered': 30, 'items.0.quantity': 260}, '50 net stock; uncovered30; 280+30-50=260.')
probe('same_day_receipt_before_commitment', {'material_requirements': [{'quantity': 100, 'already_accounted_quantity': 0, 'needed_at': '2026-04-03'}],
       'inbound': [{'quantity': 100, 'expected_at': '2026-04-03', 'status': 'confirmed'}]},
      {'items.0.quantity': 210, 'items.0.early_shortfall': 0, 'items.0.requires_expedite': False},
      'Receipt100 at day2 start covers commitment100 that day; regular70 covers first7 days.')
probe('overdue_inbound_not_reconfirmed', {'inbound': [{'quantity': 100, 'expected_at': '2026-03-31', 'status': 'confirmed'}]},
      {'items.0.quantity': 210, 'items.0.inbound_in_horizon': 0}, 'Old ETA cannot be treated as a new future receipt.')
probe('ordinary_eta_equals_first_deficit', {'on_hand': 60},
      {'items.0.quantity': 220, 'items.0.first_deficit_day': 7, 'items.0.early_shortfall': 0, 'items.0.requires_expedite': False},
      'Normal order arrives at day7 start before demand; first6 days consume60.')
probe('receipt_after_horizon_excluded', {'inbound': [{'quantity': 100, 'expected_at': '2026-04-30', 'status': 'confirmed'}]},
      {'items.0.quantity': 210, 'items.0.inbound_in_horizon': 0}, 'Horizon ends April29; April30 receipt is outside.')
probe('cancelled_receipt_excluded', {'inbound': [{'quantity': 100, 'expected_at': '2026-04-05', 'status': 'cancelled'}]},
      {'items.0.quantity': 210, 'items.0.inbound_in_horizon': 0}, 'Cancelled receipt provides no supply.')
probe('moq_multiple_box_conversion', {'on_hand': 255, 'order_unit': 'box', 'order_to_base_factor': 10, 'min_order_qty': 2, 'order_multiple': 2},
      {'items.0.quantity': 40, 'items.0.unit': 'шт'}, 'Need25 pieces; MOQ2 boxes=20; multiple2 boxes=20; final40 pieces.')
probe('moq_does_not_create_need', {'on_hand': 500, 'min_order_qty': 100, 'order_multiple': 50},
      {'items.0.quantity': 0}, 'Demand280 already covered; supplier MOQ is conditional on ordering.')
probe('price_unknown_zero_quantity', {'on_hand': 500, 'unit_cost': None},
      {'items.0.quantity': 0, 'total_cost_kzt': 0, 'approval_blocked': False}, 'No selected quantity, so unknown price consumes no budget.', request_changes={'budget_kzt': 0})
probe('unknown_price_positive_quantity', {'unit_cost': None},
      {'items.0.quantity': 210, 'total_cost_kzt': None, 'approval_blocked': True, 'rules': ['BUDGET-01']},
      'Unknown positive-order price is not zero.', request_changes={'budget_kzt': 999999})
probe('two_supplier_decimal_budget', {},
      {'known_cost_kzt': 63000, 'total_cost_kzt': 63000, 'budget_excess_kzt': 3000, 'approval_blocked': True,
       'supplier_groups': {'supplier-A': ['0001'], 'supplier-B': ['0002']}},
      'Independent total: Decimal210*100 + Decimal210*200 =63000; budget60000 leaves excess3000; suppliers remain separate.',
      case='two_suppliers_budget_total')
probe('rare_high_margin_does_not_force_stock', {},
      {'items.0.quantity': 0, 'items.0.target_stock': 0}, 'P(D=0)=0.95 exceeds economic quantile0.9.', case='economics_rare_high_margin')
probe('project_commitment_does_not_become_regular', {},
      {'items.0.forecast_mean': 0, 'items.0.target_stock': 0, 'items.0.quantity': 7}, 'Confirmed project7 stays separate from regular forecast0.', case='economics_project_only_obligation')
probe('economic_floor_respected', {},
      {'items.0.target_quantile': 0.9, 'items.0.quantity': 350}, 'Category floor0.9 overrides raw economic quantile0.1.', case='economics_category_floor')
probe('growth_is_not_applied_twice', {},
      {'items.0.forecast_mean': 336, 'items.0.quantity': 266}, 'One shared growth ID is applied only once.', case='growth_duplicate_id')

# Previously reported defects, rerun with independently stated expectations.
probe('exact_decimal_batch_boundary', {'unit': 'м', 'order_unit': 'м', 'unit_quantum': 0.01, 'on_hand': 279.9, 'min_order_qty': 0.1, 'order_multiple': 0.1},
      {'items.0.quantity': 0.1}, 'Decimal(280)-Decimal(279.9)=0.1; one exact batch.', 'medium', 'BIZ-02', 'confirmed_reference_defect')
probe('exact_decimal_budget_boundary', {'on_hand': 277, 'unit_cost': 0.1},
      {'total_cost_kzt': 0.3, 'budget_excess_kzt': 0, 'approval_blocked': False, 'rules': []},
      'Decimal(3)*Decimal(0.1)=Decimal(0.3), exactly equal to budget.', 'medium', 'BIZ-03', 'confirmed_reference_defect', {'budget_kzt': 0.3})
bad_expected = {'items.0.status': 'needs_data', 'items.0.quantity': None, 'approval_blocked': True}
for name, changes, proof in [
    ('negative_demand', {'daily_mean': [-10] * 28}, 'Regular demand cannot be negative; returns are not represented by this field.'),
    ('negative_price', {'unit_cost': -100}, 'A procurement price cannot silently reduce budget spend; no credit/return type exists here.'),
    ('negative_receipt', {'inbound': [{'quantity': -20, 'expected_at': '2026-04-05', 'status': 'confirmed'}]}, 'Confirmed receipt is a nonnegative incoming quantity.'),
    ('negative_lead_time', {'lead_time_days': -1, 'review_period_days': 29}, 'Sum(-1,29)=28 does not make negative lead time valid.'),
    ('negative_review_period', {'lead_time_days': 29, 'review_period_days': -1}, 'Review period cannot be negative even when lead+review matches horizon.'),
    ('negative_accounted_commitment', {'material_requirements': [{'quantity': 50, 'already_accounted_quantity': -20, 'needed_at': '2026-04-10'}]}, 'An already-accounted portion cannot be negative; current arithmetic invents70 from50.'),
]:
    probe(name, changes, bad_expected, proof, 'medium', 'BIZ-05', 'confirmed_reference_validation_gap', {'budget_kzt': 0} if name == 'negative_price' else None)
probe('economic_source_preserved', {'economics': {'underage_cost': 1, 'overage_cost': 1, 'horizon_days': 28, 'source': 'audit-owner-approved-v3'}},
      {'items.0.economics_source': 'audit-owner-approved-v3'}, 'The explicitly supplied economics source must survive in the recommendation.', 'medium', 'BIZ-06', 'confirmed_reference_defect')

# Newly confirmed calendar validation gaps, all with valid JSON numeric values.
for name, changes in [
    ('malformed_receipt_date', {'inbound': [{'quantity': 100, 'expected_at': '2026-04-05x', 'status': 'confirmed'}]}),
    ('malformed_commitment_date', {'material_requirements': [{'quantity': 100, 'already_accounted_quantity': 0, 'needed_at': '2026-04-02x'}]}),
]:
    probe(name, changes, bad_expected, 'Invalid ISO date must be rejected; lexical range inclusion and exact calendar equality disagree.',
          'high', 'BIZ-08', 'confirmed_reference_validation_gap')

# The accepted near-normalized distribution has a singleton support280. Either
# reject its mass or normalize to probability1; target0 is never a valid outcome.
request = deepcopy(BASE)
request['items'][0].update({'horizon_distribution': [{'quantity': 280, 'probability': 0.9999999999}],
                          'economics': {'underage_cost': 1, 'overage_cost': 0, 'horizon_days': 28, 'source': 'audit'}})
response = reference_calculate(deepcopy(request))
item = response['items'][0]
acceptable = (item['status'] == 'needs_data' and item['quantity'] is None and response['approval_blocked']) or (
    item['status'] == 'ok' and equal(item['target_stock'], 280) and equal(item['quantity'], 210))
record('accepted_mass_quantile_one', 'independent_boundary_probes', {'reject_or_singleton_target_280': True},
       {'reject_or_singleton_target_280': acceptable},
       'CDF mass0.9999999999 passes isclose(.,1); q1 traversal then falls through. Singleton280 requires target280 or explicit rejection.',
       'high', 'BIZ-09', 'confirmed_reference_defect', request, {'full_response': response})

# Ambiguities stay out of confirmed-defect counts.
probe('unknown_moq_known_multiple', {'on_hand': 69, 'min_order_qty': None, 'order_multiple': 5},
      {'items.0.quantity': 215, 'items.0.status': 'needs_review', 'approval_blocked': True},
      'If quantity is a constrained recommendation, round211 to215; README also permits provisional raw need211. Contract must choose.',
      'low', 'BIZ-07', 'contract_ambiguity')
probe('unknown_multiple_known_moq', {'on_hand': 279, 'min_order_qty': 10, 'order_multiple': None},
      {'items.0.quantity': 10, 'items.0.status': 'needs_review', 'approval_blocked': True},
      'If quantity is constrained, known MOQ10 still applies; documented provisional raw need1 is also plausible.',
      'low', 'BIZ-07', 'contract_ambiguity')

# Independent deterministic inventory path, INCLUDING the recommendation at ETA.
# The reference first_deficit_day explicitly omits that order and is not used as
# our expected residual-deficit date. Demand/receipts remain their original dates.
request = deepcopy(BY_ID['inbound_last_day']['input'])
response = reference_calculate(deepcopy(request))
p, item = request['items'][0], response['items'][0]
balance = physical = D(p['on_hand']) - D(p['reserved'])
trajectory = []
for day in range(1, 29):
    stamp = (date.fromisoformat(request['as_of']) + timedelta(days=day)).isoformat()
    existing = sum((D(x['quantity']) for x in p['inbound'] if x['status'] == 'confirmed' and x['expected_at'] == stamp), D(0))
    ordinary = D(item['quantity']) if day == p['lead_time_days'] else D(0)
    demand = D(p['daily_mean'][day - 1])
    balance += existing + ordinary - demand
    unmet = max(D(0), demand - physical - existing - ordinary)
    physical = max(D(0), physical + existing + ordinary - demand)
    trajectory.append({'day': day, 'existing_receipt': float(existing), 'ordinary_receipt': float(ordinary),
                       'demand': float(demand), 'backlog_balance': float(balance),
                       'physical_stock': float(physical), 'unmet': float(unmet)})
path_actual = {'suggested_quantity': item['quantity'], 'first_unmet_day': next(x['day'] for x in trajectory if x['unmet']),
               'unmet_quantity': sum(x['unmet'] for x in trajectory),
               'minimum_backlog': min(x['backlog_balance'] for x in trajectory),
               'SUPPLY-02_visible': 'SUPPLY-02' in item['rules']}
record('late_inbound_gap_after_normal_eta', 'independent_boundary_probes',
       {'suggested_quantity': 110, 'first_unmet_day': 19, 'unmet_quantity': 90, 'minimum_backlog': -90, 'SUPPLY-02_visible': True},
       path_actual, 'Day7 adds110; day18 ends0; days19..27 lose10 each; day28 receives100. '
       'An order200 (not110) at day7 would cover every day; warning must expose residual90. '
       'No assertion that every shortage must block approval; docs do not require this.',
       'high', 'BIZ-04', 'confirmed_reference_temporal_limitation', request,
       {'full_response': response, 'trajectory_with_recommended_order': trajectory,
        'manual_temporally_sufficient_quantity': 200})

# Metamorphic equivalence from documented stock algebra, not implementation output
# used as an oracle: two representations of fixed net50 always require230.
for reserve in (0, 20, 50, 200):
    gross_request = deepcopy(BASE)
    gross_request['items'][0].update(on_hand=50 + reserve, reserved=reserve)
    net_request = deepcopy(BASE)
    net_request['items'][0].update(on_hand=None, reserved=None, free_stock=50)
    gross = reference_calculate(gross_request)['items'][0]
    net = reference_calculate(net_request)['items'][0]
    record(f'net_gross_equivalence_reserve_{reserve}', 'metamorphic_stock_controls',
           {'gross_quantity': 230, 'net_quantity': 230, 'gross_free': 50, 'net_free': 50},
           {'gross_quantity': gross['quantity'], 'net_quantity': net['quantity'], 'gross_free': gross['free_stock'], 'net_free': net['free_stock']},
           'Adding the same reserved amount to gross stock preserves net50 and quantity280-50=230.')

# Decimal boundary sweeps: deliberately adversarial grids, not random samples of
# production demand. Multiplicity must not be mistaken for independent defects.
for tenths in range(1, 21):
    exact_need = D(tenths) / D(10)
    stock = D(280) - exact_need
    request = deepcopy(BASE)
    request['items'][0].update(unit='м', order_unit='м', unit_quantum=0.01,
                               on_hand=float(stock), min_order_qty=0.1, order_multiple=0.1)
    response = reference_calculate(request)
    exact_qty = (exact_need / D('0.1')).to_integral_value(rounding=ROUND_CEILING) * D('0.1')
    record(f'exact_batch_need_{tenths}_tenths', 'decimal_batch_sweep', float(exact_qty), response['items'][0]['quantity'],
           f'Exact Decimal need {exact_need} is already a whole multiple of0.1.', 'medium', 'BIZ-02',
           'confirmed_reference_defect', request)
for quantity in range(1, 13):
    for cents in (1, 3, 7, 10):
        price = D(cents) / D(100)
        budget = D(quantity) * price
        request = deepcopy(BASE)
        request['items'][0].update(on_hand=280 - quantity, unit_cost=float(price))
        request['budget_kzt'] = float(budget)
        response = reference_calculate(request)
        record(f'equal_budget_q{quantity}_cents{cents}', 'decimal_money_sweep',
               {'approval_blocked': False, 'rules': []}, selected(response, ['approval_blocked', 'rules']),
               f'Decimal quantity{quantity} * price{price} = budget{budget}.', 'medium', 'BIZ-03',
               'confirmed_reference_defect', request,
               {'calculated_cost': response['total_cost_kzt'], 'reported_excess': response['budget_excess_kzt']})

# Adversarial calculator responses are DUT outputs for checker detection testing;
# reference_calculate supplies a baseline only, never the expected rejection.
mutations = []
def mutate_status(result):
    for item in result['items']:
        if item['status'] == 'ok':
            item['status'] = 'needs_data'

def mutate_rules(result):
    for item in result['items']:
        item['rules'] = ' '.join(item['rules'])

def mutate_supplier(result):
    for item in result['items']:
        item['supplier_id'] = 'wrong-supplier'

def mutate_reason(result):
    for item in result['items']:
        item['reason'] = True

def mutate_quantity(result):
    for item in result['items']:
        if item['quantity'] is not None:
            item['quantity'] = -1

def mutate_unit(result):
    for item in result['items']:
        if item['quantity'] is not None:
            item['unit'] = 'wrong-physical-unit'

for name, mutate, proof in [
    ('status_quantity_approval_conflict', mutate_status, 'needs_data requires null quantity and blocked approval.'),
    ('rules_wrong_type', mutate_rules, 'Rule IDs must be a list, not a string containing expected substrings.'),
    ('supplier_identity_conflict', mutate_supplier, 'Item supplier must agree with request and supplier_groups.'),
    ('reason_wrong_type', mutate_reason, 'Explanation must be text, not boolean true.'),
    ('negative_quantity_response', mutate_quantity, 'Any non-null recommended quantity must be nonnegative.'),
    ('wrong_physical_unit_response', mutate_unit, 'Recommendation units must preserve the input base unit.'),
]:
    changed = set()
    def adapter(request):
        response = reference_calculate(request)
        before = deepcopy(response)
        mutate(response)
        if response != before:
            changed.add(json.dumps(request, sort_keys=True))
        return response
    outcomes = check_cases(adapter)
    changed_ids = {c['id'] for c in CASES if json.dumps(c['input'], sort_keys=True) in changed}
    false_pass = [r['id'] for r in outcomes if r['id'] in changed_ids and r['passed']]
    mutations.append({'id': name, 'severity': 'high', 'classification': 'confirmed_checker_defect', 'finding_id': 'BIZ-01',
                      'expected': {'false_pass_cases': 0}, 'actual': {'false_pass_cases': len(false_pass)},
                      'passed': not false_pass, 'rationale': proof,
                      'total_cases': len(outcomes), 'mutated_cases': len(changed_ids),
                      'checker_passed': sum(r['passed'] for r in outcomes), 'false_pass_ids': false_pass,
                      'rejected_mutated_ids': [r['id'] for r in outcomes if r['id'] in changed_ids and not r['passed']]})

findings = [
    {'id': 'BIZ-01', 'severity': 'high', 'status': 'reconfirmed', 'scope': 'checker',
     'title': 'Subset comparison does not enforce a shared response schema or cross-field invariants.', 'source_lines': [153, 174, 187]},
    {'id': 'BIZ-02', 'severity': 'medium', 'status': 'reconfirmed', 'scope': 'reference_only',
     'title': 'Float subtraction happens before Decimal batch rounding, causing a full extra batch.', 'source_lines': [13, 101, 112]},
    {'id': 'BIZ-03', 'severity': 'medium', 'status': 'reconfirmed', 'scope': 'reference_only',
     'title': 'Float monetary arithmetic can block an exactly sufficient budget.', 'source_lines': [136, 143]},
    {'id': 'BIZ-04', 'severity': 'high', 'status': 'reconfirmed', 'scope': 'reference_only',
     'title': 'Receipt late in horizon leaves post-ETA shortages unflagged despite normal recommendation.', 'source_lines': [98, 113, 124]},
    {'id': 'BIZ-05', 'severity': 'medium', 'status': 'reconfirmed_and_extended', 'scope': 'reference_only',
     'title': 'Negative quantities, prices, lead/review periods and accounted obligations are accepted.', 'source_lines': [40, 44, 97, 100, 136]},
    {'id': 'BIZ-06', 'severity': 'medium', 'status': 'reconfirmed', 'scope': 'reference_only',
     'title': 'Economic source is replaced by a fixed synthetic label.', 'source_lines': [134]},
    {'id': 'BIZ-07', 'severity': 'low', 'status': 'ambiguity', 'scope': 'reference_only',
     'title': 'Known supplier constraint is ignored when the other constraint is unknown; provisional quantity semantics need clarification.', 'source_lines': [101, 103]},
    {'id': 'BIZ-08', 'severity': 'high', 'status': 'new', 'scope': 'reference_only',
     'title': 'Malformed receipt/commitment dates affect totals but disappear from the calendar path.', 'source_lines': [95, 98, 118, 121]},
    {'id': 'BIZ-09', 'severity': 'high', 'status': 'new', 'scope': 'reference_only',
     'title': 'Probability tolerance and q=1 traversal disagree: deterministic demand280 yields target0.', 'source_lines': [60, 65, 70, 74, 77]},
]
groups = {}
for group in sorted({r['group'] for r in rows}):
    subset = [r for r in rows if r['group'] == group]
    groups[group] = {'checks': len(subset), 'matches': sum(r['passed'] for r in subset),
                     'mismatches': sum(not r['passed'] for r in subset)}
confirmed_rows = [r for r in rows if r['classification'].startswith('confirmed_') and not r['passed']]
ambiguities = [r for r in rows if r['classification'] == 'contract_ambiguity']
result = {
    'schema_version': 'business-independent-audit-v2', 'audit_date': '2026-09-23',
    'integration_status': 'reference_only', 'production_calculator_tested': False,
    'calculator': 'model.testbed.business:reference_calculate',
    'git_commit_at_run': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
    'python_version': platform.python_version(),
    'source_sha256': {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in (
        ROOT/'business.py', ROOT/'fixtures/business_cases.json', ROOT/'freeze.json',
        ROOT/'README.md', Path('docs/decision-rules.md'), Path('docs/model-backend-spec.md'))},
    'oracle': 'Manual fixture quantity/status ledger, explicit boundary expectations, exact Decimal algebra and independent inventory path; no reference output used as oracle.',
    'forecast_accuracy': {'evaluated_here': False, 'WAPE': None, 'MAE': None, 'bias': None,
                          'reason': 'Procurement correctness and checker detection are distinct from forecast accuracy.'},
    'original_fixture_check': {'checks': len(original), 'matches': sum(r['passed'] for r in original),
                               'mismatches': sum(not r['passed'] for r in original)},
    'group_counts': groups, 'checks': rows, 'checker_mutations': mutations, 'findings': findings,
    'counts': {'confirmed_finding_categories': sum(f['status'] != 'ambiguity' for f in findings),
               'ambiguity_categories': sum(f['status'] == 'ambiguity' for f in findings),
               'confirmed_failing_probe_rows': len(confirmed_rows), 'ambiguity_probe_rows': len(ambiguities),
               'checker_mutation_campaigns': len(mutations),
               'checker_mutation_campaigns_with_false_pass': sum(not r['passed'] for r in mutations)},
    'limitations': ['No external production calculator is present or tested.',
                    'Adversarial deterministic checks are not a representative reliability sample.',
                    'The54-item independent ledger covers quantity/status only, not every response field.',
                    'Raw prices are interpreted as per base unit; price-per-order-unit semantics are not defined by the fixture contract.',
                    'Revision approval, ingestion idempotency, database effects, real stockouts and customer behavior are outside this local calculator.'],
}
DEST.mkdir(parents=True, exist_ok=True)
(DEST/'results.json').write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)+'\n')

table = '\n'.join(f"| {name} | {v['checks']} | {v['matches']} | {v['mismatches']} |" for name, v in groups.items())
mutation_table = '\n'.join(f"| {m['id']} | {m['mutated_cases']} | {m['actual']['false_pass_cases']} | {m['checker_passed']}/{m['total_cases']} |" for m in mutations)
report = f'''# Независимые бизнес-проверки, аудит review-2

Область: замороженный `model/testbed/business.py`, 54 фикстуры, README,
`docs/decision-rules.md` и `docs/model-backend-spec.md`. Статус **reference_only**:
производственный расчётный модуль не подключён. Эти результаты не доказывают его
корректность и не измеряют точность прогноза.

## Воспроизведение

Из корня worktree:

```sh
bash model/testbed/audits/review-2/business/run.sh
```

Нужен Python 3.10+; исполнено на Python {platform.python_version()}. Скрипт пишет только
`report.md` и `results.json` рядом с собой, не добавляет `.py`/bytecode и не меняет
freeze. Найденные дефекты сохраняются как результаты; exit0 означает успешное
исполнение аудита. Ошибка самого раннера завершает команду ненулевым кодом.
В JSON есть SHA-256 исходников/фикстур/freeze, входы дополнительных проб, ожидание,
факт, severity и отдельные счётчики. Исходный Git SHA: `{result['git_commit_at_run']}`.

## Счётчики без общего «процента качества»

Оригинальный checker на референсе: **{result['original_fixture_check']['matches']}/54**.
Самостоятельный арифметический реестр покрывает quantity/status всех 54 примеров;
ожидания заданы вручную, а не взяты из `case.expected`/ответа референса.

| Группа | Проверок | Совпало | Не совпало |
|---|---:|---:|---:|
{table}

Несовпадения adversarial-проб являются находками, а не отказом аудита. В отдельной
группе boundary есть две неоднозначности, которые не считаются доказанными дефектами.
Sweep повторяет один и тот же риск на разных точных границах: число таких строк не
равно числу независимых дефектов. Найдено {result['counts']['confirmed_finding_categories']}
подтверждённых категорий проблем и одна категория неоднозначности. Пробы специально
нацелены на риски; доля совпадений не оценивает надёжность в эксплуатации.

**WAPE, MAE и смещение здесь не вычисляются.** В JSON они null с явной причиной.
54/54 бизнес-примеров не означает ни 100% точности прогноза, ни проверенный production.

## Общая валидация ответа checker: BIZ-01, high

Повторно подтверждены четыре старые мутации и добавлены отрицательное quantity и
подмена физической единицы. Для каждой модифицированного недопустимого ответа
нормативное ожидание — отклонение, независимо от неполноты конкретного expected.

| Мутация | Изменённых ответов | Ложных PASS | Отчёт checker |
|---|---:|---:|---:|
{mutation_table}

`compare` проверяет только перечисленные поля, а последняя проверка требует лишь
truthy reason/rules. Поэтому в зависимости от фикстуры остаются незамеченными
некорректные типы, поставщик, отрицательное количество, единица и конфликт
status/quantity/approval. Нужен общий валидатор схемы и инвариантов перед subset
сравнением. Дефект относится к тестовой среде; это не доказательство наличия таких
ошибок во внешнем модуле.

## Подтверждение прежних находок reference_only

- **BIZ-02, medium:** Decimal(280) − Decimal(279.9) = 0.1 метра, но результат **0.2**.
  Float-вычитание выполняется до Decimal округления. Из20 точных границ партии
  несовпадений: **{groups['decimal_batch_sweep']['mismatches']}**.
- **BIZ-03, medium:** 3 × 0.1 = бюджет0.3; получены стоимость0.30000000000000004,
  `BUDGET-02` и блокировка. Из48 равных денежных границ ложных блокировок:
  **{groups['decimal_money_sweep']['mismatches']}**. Нужно фиксировать денежную точность
  до сравнения бюджета; числовой допуск checker не исправляет дискретную блокировку.
- **BIZ-04, high:** при запасе70, спросе10/день, ordinary ETA7 и inbound100 на день28
  предлагается110. Независимая траектория **включает** эти110 в начале дня7:
  день18 заканчивается нулём, дни19–27 теряют90 единиц спроса. `SUPPLY-02` отсутствует.
  Заказ200 на день7 обеспечил бы весь период; это не призыв бесконтрольно увеличивать
  заказ, а доказательство временной недостаточности агрегированного расчёта.
  `first_deficit_day=8` у референса рассчитан без заказа и сам по себе не ошибочен.
  Документы не требуют блокировать утверждение при любом дефиците, поэтому
  `approval_blocked=false` не объявлен самостоятельным нарушением.
- **BIZ-05, medium:** приняты отрицательные спрос, цена и поступление. Дополнительно
  подтверждены отрицательные lead/review periods при сохранении их суммы28 и
  отрицательная уже учтённая часть обязательства. Последняя превращает обязательство50
  в неучтённые70; lead−1 + review29 принимаются как допустимый срок.
- **BIZ-06, medium:** переданный `audit-owner-approved-v3` заменяется на
  `synthetic-profile`; происхождение экономического параметра теряется.

## Новые конкретные находки reference_only

**BIZ-08 — high: недопустимые даты влияют на итог, но исчезают из календаря.**
`expected_at=2026-04-05x`, quantity100 проходит строковый фильтр горизонта;
количество заказа уменьшается с210 до110. Такая дата никогда не равна реальному
дню траектории, поэтому физическое поступление100 не происходит в её расчёте.
Аналогично `needed_at=2026-04-02x`, quantity100 увеличивает заказ до310, но полностью
исчезает из дневных обязательств; ранний дефицит не отмечен. Оба входа принимаются
как `ok` и разрешены к утверждению. Даты нужно разбирать и проверять до арифметики.

**BIZ-09 — high: допустимая погрешность суммы вероятностей обнуляет целевой запас.**
У singleton-распределения quantity280, probability0.9999999999 сумма проходит
`isclose(sum,1)`. Экономика underage1/overage0 даёт q=1. В проходе по CDF используется
другой допуск1e−12, накопленная масса не достигает1, а target остаётся0. В результате
детерминированный спрос280, запас70 получают quantity0 и разрешённое утверждение.
Независимый oracle допускает **либо отказ с needs_data, либо нормализацию с target280
и quantity210**; оба допустимых поведения исключают наблюдаемый target0.
Нулевые отдельные underage/overage разрешены: проверка запрещает отрицательное
значение и нулевую сумму, а docs задаёт формулу underage/(underage+overage) без
исключения её экономических границ. Категориальный quantile проверяется отдельно;
ограничение0<q<1 категории не применяется к квантилю из экономики.

## Неоднозначности и границы охвата

**BIZ-07, low, ambiguity:** при неизвестном MOQ и известной кратности5 возвращается
необработанная потребность211; при неизвестной кратности и MOQ10 — потребность1.
Контрольная интерпретация «уже допустимая партия» требует215 и10 соответственно.
Но README допускает базовую потребность с needs_review; блокировка работает.
Эти два случая не включены в подтверждённые дефекты. Также следует явно определить
единицу закупочной цены при order_unit!=base_unit; в этом аудите цена трактуется на
базовую единицу, без выдумывания коэффициента цены.

Положительные контроли проверяют net/gross reserve, просроченные обязательства,
учтённую часть, receipt до demand в один день, ETA на первой дате дефицита,
отменённые/просроченные/вне горизонта поступления, MOQ/кратность/конверсию коробок,
нулевой заказ при излишке, неизвестную цену, редкий спрос с высокой маржой,
подтверждённый проект отдельно от регулярного спроса, категориальный минимум и
однократное применение прироста. Четыре net/gross метаморфные проверки дают
одинаковое вручную рассчитанное количество230 при резерве0,20,50,200.

Revision/HTTP approval, ingestion-idempotency, БД, реальные stockout и клиенты здесь
не проверены. Исходный код, freeze, фикстуры и модель не исправлялись: это аудит
замороженного кандидата с воспроизводимыми доказательствами и рекомендациями.
'''
(DEST/'report.md').write_text(report)
print(json.dumps({'output': str(DEST), 'integration_status': 'reference_only',
                  'original_fixture_check': result['original_fixture_check'], 'group_counts': groups,
                  'counts': result['counts'], 'forecast_accuracy_evaluated': False}, ensure_ascii=False))
PY
