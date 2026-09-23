#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname -- "$0")/../../../.."
PYTHONDONTWRITEBYTECODE=1 python3 - <<'PY'
"""Read-only audit of frozen source; output only business.json beside this script."""
from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal, ROUND_CEILING
import hashlib
import json
from pathlib import Path

from model.testbed.business import check_cases, reference_calculate

ROOT = Path('model/testbed')
DEST = ROOT / 'audits/2026-09-23/business.json'
cases = json.loads((ROOT / 'fixtures/business_cases.json').read_text())['cases']
by_id = {c['id']: c for c in cases}
base = by_id['stock_base']['input']
D = lambda x: Decimal(str(x))

# Independently written arithmetic ledger, not imported from fixture expected or
# reference_calculate. Distribution quantiles and quantities were reviewed against
# decision-rules.md. None denotes deliberate needs_data, not zero demand.
ledger = {
    'stock_base': (210, '280 - 70'),
    'stock_increased': (180, '280 - 100'),
    'stock_surplus_no_moq_order': (0, 'max(0, 280 - 500); MOQ cannot create need'),
    'reserve_gross': (230, '280 - (70 - 20)'),
    'reserve_net_not_twice': (230, '280 - 50; provided free_stock already net'),
    'inbound_timely': (110, '280 - 70 - 100'),
    'inbound_late_gap': (150, '280 - 30 - 100; temporal adequacy audited separately'),
    'inbound_before_gap': (150, '280 - 30 - 100'),
    'ordinary_order_does_not_erase_gap': (280, '280 - 0'),
    'inbound_outside_horizon': (210, '280 - 70; receipt after horizon excluded'),
    'inbound_cancelled': (210, '280 - 70; cancelled receipt excluded'),
    'inbound_last_day': (110, '280 - 70 - 100; horizon total only, not timely adequacy'),
    'moq_and_multiple': (15, 'ceil(max(280-275, 12)/5)*5'),
    'multiple_exact': (210, '(280 - 70)/7 = 30 exact batches'),
    'moq_unknown': (210, '280 - 70, provisional quantity, needs_review'),
    'multiple_unknown': (210, '280 - 70, provisional quantity, needs_review'),
    'category_A': (210, 'F(140)=0.2, F(280)=0.8; q=0.8 => 280 - 70'),
    'category_B': (350, 'F(280)=0.8 < q=0.9 => 420 - 70'),
    'external_growth': (266, '28*10*1.2 - 70 = 336 - 70'),
    'growth_partial_period': (238, '14*12 + 14*10 - 70 = 308 - 70'),
    'growth_outside_horizon': (210, '280 - 70; no overlapping date'),
    'growth_already_in_forecast': (266, '336 - 70; do not apply g-1 twice'),
    'growth_duplicate_id': (266, '280*1.2 - 70; g-1 counted once'),
    'material_only_uncovered': (260, '280 + (50-20) - (70-20)'),
    'material_fully_reserved': (230, '280 + (20-20) - (70-20)'),
    'material_after_horizon': (210, '280 - 70; future obligation excluded'),
    'material_due_before_supply': (310, '280 + 100 - 70; date gap remains'),
    'economics_expensive_low_contribution': (70, 'q=10/100=0.1 => 140 - 70'),
    'economics_cheap_high_contribution': (350, 'q=90/100=0.9 => 420 - 70'),
    'economics_category_floor': (350, 'max(10/100, 0.9)=0.9 => 420 - 70'),
    'economics_rare_high_margin': (0, 'P(D=0)=0.95 >= q=0.9 => target 0'),
    'economics_frequent_low_unit_margin': (280, 'q=1/2; deterministic D=280'),
    'economics_project_only_obligation': (7, 'regular target 0 + confirmed project 7'),
    'budget_equal': (210, '280 - 70; 210*100 = budget 21000'),
    'budget_exceeded': (210, '280 - 70; 210*100 - 20000 = 1000'),
    'budget_unknown_price': (210, '280 - 70; quantity known, cost unknown'),
    'unknown_price_without_budget': (210, '280 - 70; total cost remains unknown'),
    'missing_stock': (None, 'stock cannot be inferred'),
    'stale_stock': (None, 'stock_current is false'),
    'missing_reserve': (None, 'gross stock without reserve is insufficient'),
    'missing_lead': (None, 'lead time is absent'),
    'missing_policy': (None, 'neither category policy nor economics'),
    'invalid_horizon': (None, '7+20 != 28'),
    'missing_demand': (None, 'one daily demand is missing'),
    'economics_wrong_horizon': (None, 'economics 7 days != demand 28 days'),
    'missing_conversion': (None, 'box to pieces factor absent'),
    'invalid_multiple': (None, 'batch multiple cannot be 0'),
    'unit_boxes_to_pieces': (40, 'need25, MOQ20 pieces, multiple20 => 40 pieces'),
    'unit_fractional_meters': (0.3, 'need0.26, MOQ0.1, multiple0.1 => 0.3 metres'),
    'unit_missing_quantum': (None, 'physical quantum is absent'),
    'unit_incompatible_multiple': (None, 'half-piece batch conflicts with indivisible piece'),
    'two_suppliers_budget_total': ([210, 210], '210*100 + 210*200 = 63000; excess3000'),
    'material_unit_mismatch': (None, 'unconverted box obligation cannot be added to pieces'),
    'inbound_unit_mismatch': (None, 'unconverted box receipt cannot be added to pieces'),
}
assert set(ledger) == set(by_id)

def equivalent(a, b):
    if a is None or b is None:
        return a is b
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(D(a) - D(b)) <= D('0.00000001')
    return type(a) is type(b) and a == b

independent = []
for c in cases:
    quantities, proof = ledger[c['id']]
    quantities = quantities if isinstance(quantities, list) else [quantities]
    expected = [{'quantity': q, 'status': ('needs_data' if q is None else
                 'needs_review' if c['id'] in {'moq_unknown', 'multiple_unknown'} else 'ok')}
                for q in quantities]
    actual = reference_calculate(deepcopy(c['input']))
    failures = []
    for i, row in enumerate(expected):
        for k, value in row.items():
            if not equivalent(value, actual['items'][i].get(k)):
                failures.append(f'actual.items[{i}].{k} differs')
            if k in c['expected']['items'][i] and not equivalent(value, c['expected']['items'][i][k]):
                failures.append(f'fixture.expected.items[{i}].{k} differs')
    independent.append({'id': c['id'], 'proof': proof, 'independent_expected': expected,
                        'actual': [{k: item.get(k) for k in ('quantity', 'status')} for item in actual['items']],
                        'passed': not failures, 'failures': failures})

original = check_cases(reference_calculate)
mutations = []
def mutation_probe(name, mutate, description):
    changed = set()
    def calculator(request):
        response = reference_calculate(request)
        before = deepcopy(response)
        mutate(response)
        if response != before:
            changed.add(json.dumps(request, sort_keys=True))
        return response
    rows = check_cases(calculator)
    mutated_ids = [c['id'] for c in cases if json.dumps(c['input'], sort_keys=True) in changed]
    false_pass_ids = [r['id'] for r in rows if r['id'] in mutated_ids and r['passed']]
    mutations.append({'id': name, 'classification': 'confirmed_checker_contract_gap',
                      'expected': 'Every changed invalid response must be rejected',
                      'description': description, 'total_cases': len(rows),
                      'checker_passed': sum(r['passed'] for r in rows),
                      'mutated_cases': len(mutated_ids), 'false_pass_cases': len(false_pass_ids),
                      'false_pass_ids': false_pass_ids,
                      'rejected_ids': [r['id'] for r in rows if not r['passed']]})

def bad_status(response):
    for item in response['items']:
        if item['status'] == 'ok':
            item['status'] = 'needs_data'
def bad_rule_type(response):
    for item in response['items']:
        item['rules'] = ' '.join(item['rules'])
def bad_supplier(response):
    for item in response['items']:
        item['supplier_id'] = 'WRONG-SUPPLIER'
def bad_reason_type(response):
    for item in response['items']:
        item['reason'] = True
mutation_probe('status_quantity_approval_inconsistency', bad_status,
               'Numeric quantity with needs_data; existing approval_blocked unchanged.')
mutation_probe('rules_string_instead_of_list', bad_rule_type,
               'String membership passes contains; nonempty malformed type passes explanation check.')
mutation_probe('supplier_row_group_mismatch', bad_supplier,
               'All row supplier_id changed; supplier_groups deliberately left unchanged.')
mutation_probe('reason_boolean_instead_of_text', bad_reason_type,
               'True is truthy but cannot explain a procurement recommendation.')

probes = []
def evaluate_probe(name, patch, expected, classification, rationale, top_patch=None):
    request = deepcopy(base)
    request['items'][0].update(deepcopy(patch))
    if top_patch:
        request.update(deepcopy(top_patch))
    actual = reference_calculate(request)
    selected = {key: (actual['items'][0].get(key[5:]) if key.startswith('item.') else actual.get(key))
                for key in expected}
    failed = [key for key in expected if not equivalent(expected[key], selected[key])]
    probes.append({'id': name, 'classification': classification, 'input': request,
                   'expected': expected, 'actual_selected': selected, 'actual': actual,
                   'passed': not failed, 'differing_fields': failed, 'rationale': rationale})
    return probes[-1]

# Decimal oracle starts from original input strings, before subtraction/multiplication.
decimal_need = D('280') - D('279.9')
decimal_quantity = (decimal_need / D('0.1')).to_integral_value(rounding=ROUND_CEILING) * D('0.1')
evaluate_probe('decimal_exact_batch_boundary',
               {'unit': 'м', 'order_unit': 'м', 'unit_quantum': 0.01, 'min_order_qty': 0.1,
                'order_multiple': 0.1, 'on_hand': 279.9},
               {'item.quantity': float(decimal_quantity)}, 'confirmed_reference_defect',
               f'Decimal(280)-Decimal(279.9)={decimal_need}; exact 0.1 batch => {decimal_quantity}.')
decimal_cost = D('3') * D('0.1')
evaluate_probe('decimal_exact_budget_boundary', {'on_hand': 277, 'unit_cost': 0.1},
               {'total_cost_kzt': float(decimal_cost), 'budget_excess_kzt': 0,
                'approval_blocked': False, 'rules': []}, 'confirmed_reference_defect',
               'Decimal(3)*Decimal(0.1)=Decimal(0.3), equal to budget; no BUDGET-02.',
               {'budget_kzt': 0.3})
evaluate_probe('negative_daily_demand', {'daily_mean': [-10] * 28},
               {'item.status': 'needs_data', 'item.quantity': None, 'approval_blocked': True},
               'confirmed_reference_validation_gap', 'Regular demand cannot be negative; this contract has no return events.')
evaluate_probe('negative_price', {'unit_cost': -100},
               {'item.status': 'needs_data', 'item.quantity': None, 'approval_blocked': True},
               'confirmed_reference_validation_gap', 'A negative procurement unit price cannot silently decrease the budget total.',
               {'budget_kzt': 0})
evaluate_probe('negative_inbound', {'inbound': [{'quantity': -20, 'expected_at': '2026-04-05', 'status': 'confirmed'}]},
               {'item.status': 'needs_data', 'item.quantity': None, 'approval_blocked': True},
               'confirmed_reference_validation_gap', 'Confirmed receipt quantity must be nonnegative; returns require a separate typed movement.')
evaluate_probe('economic_provenance', {'economics': {'underage_cost': 1, 'overage_cost': 1,
               'horizon_days': 28, 'source': 'audit-owner-approved-v3'}},
               {'item.economics_source': 'audit-owner-approved-v3'}, 'confirmed_reference_defect',
               'The supplied economics source must be retained rather than replaced with synthetic-profile.')
evaluate_probe('unknown_moq_known_multiple', {'on_hand': 69, 'min_order_qty': None, 'order_multiple': 5},
               {'item.quantity': 215, 'item.status': 'needs_review', 'approval_blocked': True},
               'reference_contract_ambiguity',
               'Known multiple 5 can be honoured: ceil((280-69)/5)*5=215. Current output is provisional raw need211; approval remains blocked.')
evaluate_probe('unknown_multiple_known_moq', {'on_hand': 279, 'min_order_qty': 10, 'order_multiple': None},
               {'item.quantity': 10, 'item.status': 'needs_review', 'approval_blocked': True},
               'reference_contract_ambiguity',
               'Known minimum10 can be honoured even if multiple is unknown. Current provisional need1 remains blocked; define contract explicitly.')

# Additional passing boundaries were absent from the frozen fixture set.
evaluate_probe('net_stock_without_gross_or_reserve', {'on_hand': None, 'reserved': None, 'free_stock': 50},
               {'item.quantity': 230, 'item.free_stock': 50, 'item.status': 'ok'},
               'additional_boundary_control', 'Explicit free_stock50 is already net; 280-50=230.')
evaluate_probe('overdue_obligation', {'material_requirements': [{'requirement_id': 'overdue', 'quantity': 80,
               'already_accounted_quantity': 0, 'needed_at': '2026-03-31', 'unit': 'шт'}]},
               {'item.quantity': 290, 'item.first_deficit_day': 1, 'item.early_shortfall': 70,
                'item.requires_expedite': True}, 'additional_boundary_control',
               'Overdue80 is due day1: 280+80-70=290; day6 balance70-60-80=-70.')
evaluate_probe('unreconfirmed_overdue_inbound', {'inbound': [{'quantity': 100, 'expected_at': '2026-03-31', 'status': 'confirmed'}]},
               {'item.quantity': 210, 'item.inbound_in_horizon': 0}, 'additional_boundary_control',
               'Past ETA without an updated future ETA cannot reduce need.')
evaluate_probe('ordinary_receipt_on_first_deficit_day', {'on_hand': 60},
               {'item.quantity': 220, 'item.first_deficit_day': 7, 'item.early_shortfall': 0,
                'item.requires_expedite': False}, 'additional_boundary_control',
               'Start-of-day7 ordinary receipt precedes end-of-day7 demand, so there is no pre-ETA shortage.')
evaluate_probe('unknown_price_zero_order', {'on_hand': 500, 'unit_cost': None},
               {'item.quantity': 0, 'total_cost_kzt': 0, 'approval_blocked': False, 'rules': []},
               'additional_boundary_control', 'Unknown price for a zero quantity does not consume budget.', {'budget_kzt': 0})

# Independent path includes the actual suggested normal order at its own ETA.
# Both backlog accounting and lost-sales accounting are recorded, distinguishing
# this from the reference's explicitly WITHOUT-order first_deficit_day.
request = deepcopy(by_id['inbound_last_day']['input'])
actual = reference_calculate(request)
p, r = request['items'][0], actual['items'][0]
backlog_balance = D(p['on_hand']) - D(p['reserved'])
physical_balance = backlog_balance
trajectory = []
for day in range(1, request['horizon_days'] + 1):
    stamp = (date.fromisoformat(request['as_of']) + timedelta(days=day)).isoformat()
    existing = sum((D(x['quantity']) for x in p['inbound'] if x['status'] == 'confirmed' and x['expected_at'] == stamp), D(0))
    ordinary = D(r['quantity']) if day == p['lead_time_days'] else D(0)
    demand = D(p['daily_mean'][day - 1])
    backlog_balance += existing + ordinary - demand
    available = physical_balance + existing + ordinary
    unmet = max(D(0), demand - available)
    physical_balance = max(D(0), available - demand)
    trajectory.append({'day': day, 'date': stamp, 'existing_inbound': float(existing),
                       'ordinary_order_receipt': float(ordinary), 'regular_demand': float(demand),
                       'backlog_balance_end': float(backlog_balance),
                       'physical_balance_end': float(physical_balance), 'unmet_demand': float(unmet)})
unmet = sum(x['unmet_demand'] for x in trajectory)
probes.append({'id': 'late_inbound_residual_gap_with_suggested_order',
               'classification': 'confirmed_reference_temporal_limitation', 'input': request,
               'expected': {'minimum_backlog_balance': -90, 'first_unmet_day': 19,
                            'unmet_quantity': 90, 'SUPPLY-02_visible': True},
               'actual_selected': {'minimum_backlog_balance': min(x['backlog_balance_end'] for x in trajectory),
                                   'first_unmet_day': next(x['day'] for x in trajectory if x['unmet_demand']),
                                   'unmet_quantity': unmet, 'SUPPLY-02_visible': 'SUPPLY-02' in r['rules'],
                                   'first_deficit_day_without_order': r['first_deficit_day'],
                                   'requires_expedite': r['requires_expedite'],
                                   'approval_blocked': actual['approval_blocked']},
               'actual': actual, 'trajectory_with_suggested_order': trajectory,
               'passed': 'SUPPLY-02' in r['rules'], 'differing_fields': ['SUPPLY-02_visible'],
               'rationale': 'Order110 arrives start day7; day18 ends at0; days19..27 lose10/day; existing100 arrives day28. '
                            '90 units cannot be served on time. Reference first_deficit_day8 is WITHOUT proposed order and is not itself erroneous. '
                            'requires_expedite only covers d<lead_time; it is not a general certificate of feasible supply. '
                            'Documentation does not explicitly require approval_blocked for timing gaps, so unblocked status alone is not declared a contract violation.'})

findings = [
    {'id': 'BIZ-01', 'severity': 'high', 'classification': 'confirmed_checker_defect',
     'location': 'model/testbed/business.py:153',
     'title': 'Subset assertions lack common response schema and cross-field invariants',
     'evidence': [p['id'] for p in mutations],
     'impact': 'Every mutated adapter still reports54/54; row supplier groups, status/quantity/approval, reason and rules types are not enforced.'},
    {'id': 'BIZ-02', 'severity': 'medium', 'classification': 'confirmed_reference_defect',
     'location': 'model/testbed/business.py:101', 'additional_location': 'model/testbed/business.py:13',
     'title': 'Binary-float subtraction before Decimal rounds an exact batch up twice',
     'evidence': ['decimal_exact_batch_boundary'], 'impact': '0.1 metres required but0.2 recommended; current fractional fixture does not hit an exact boundary.'},
    {'id': 'BIZ-03', 'severity': 'medium', 'classification': 'confirmed_reference_defect',
     'location': 'model/testbed/business.py:136', 'additional_location': 'model/testbed/business.py:143',
     'title': 'Float currency comparison falsely blocks an equal budget',
     'evidence': ['decimal_exact_budget_boundary'], 'impact': '3*0.1 exceeds0.3 only in binary representation; BUDGET-02 emitted incorrectly.'},
    {'id': 'BIZ-04', 'severity': 'high', 'classification': 'confirmed_reference_temporal_limitation',
     'location': 'model/testbed/business.py:98', 'additional_location': 'model/testbed/business.py:124',
     'title': 'Late supply can leave a shortage after normal-order ETA without SUPPLY-02',
     'evidence': ['late_inbound_residual_gap_with_suggested_order'],
     'impact': 'Existing inbound_last_day passes despite90 unmet units even after ordering suggested110 at day7; requires_expedite=False cannot mean all demand is covered.'},
    {'id': 'BIZ-05', 'severity': 'medium', 'classification': 'confirmed_reference_validation_gap',
     'location': 'model/testbed/business.py:44', 'additional_location': 'model/testbed/business.py:136',
     'title': 'Negative demand, receipt quantity and price accepted as valid procurement data',
     'evidence': ['negative_daily_demand', 'negative_price', 'negative_inbound'],
     'impact': 'Missing-value fixtures do not establish numeric-domain validation; negative price can make budget usage negative.'},
    {'id': 'BIZ-06', 'severity': 'medium', 'classification': 'confirmed_reference_defect',
     'location': 'model/testbed/business.py:134', 'title': 'Economics provenance is replaced with a fixed synthetic label',
     'evidence': ['economic_provenance'], 'impact': 'The supplied scenario/source cannot be traced from the output.'},
    {'id': 'BIZ-07', 'severity': 'low', 'classification': 'reference_contract_ambiguity',
     'location': 'model/testbed/business.py:103', 'title': 'One unknown supplier constraint causes the known constraint to be ignored',
     'evidence': ['unknown_moq_known_multiple', 'unknown_multiple_known_moq'],
     'impact': 'Returned provisional quantity violates the known MOQ or multiple, but needs_review and approval_blocked are correctly set. Clarify whether quantity is raw need or a constrained recommendation.'},
]
result = {
    'schema_version': 'testbed-business-audit-v1', 'audit_date': '2026-09-23',
    'calculator': 'model.testbed.business:reference_calculate', 'integration_status': 'reference_only',
    'scope': 'Frozen source and fixture review; no model/generator/final-seed tuning.',
    'source_sha256': {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in
                      [ROOT / 'business.py', ROOT / 'fixtures/business_cases.json', ROOT / 'freeze.json',
                       Path('docs/requirements-matrix.md'), Path('docs/decision-rules.md')]},
    'original_fixture_checks': {'passed': sum(x['passed'] for x in original), 'total': len(original),
                                'failures': [x for x in original if not x['passed']]},
    'independent_quantity_status_checks': {'passed': sum(x['passed'] for x in independent),
                                         'total': len(independent), 'cases': independent},
    'adversarial_checker_mutations': mutations,
    'boundary_probes': probes, 'findings': findings,
    'counts': {'checker_mutation_probes': len(mutations), 'boundary_probes': len(probes),
               'boundary_matches': sum(p['passed'] for p in probes),
               'confirmed_reference_defect_or_gap_probes': sum(not p['passed'] and p['classification'] != 'reference_contract_ambiguity' for p in probes),
               'contract_ambiguity_probes': sum(p['classification'] == 'reference_contract_ambiguity' for p in probes)},
    'interpretation': 'These are deterministic procurement checks and mutation-detection observations, not forecast accuracy or a combined quality percentage. Production calculator is not connected.',
}
DEST.parent.mkdir(parents=True, exist_ok=True)
DEST.write_text(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + '\n')
print(json.dumps({'output': str(DEST), 'original_passed': result['original_fixture_checks']['passed'],
                  'independent_passed': result['independent_quantity_status_checks']['passed'],
                  'counts': result['counts'],
                  'mutations': [{k: p[k] for k in ('id', 'checker_passed', 'mutated_cases', 'false_pass_cases')} for p in mutations]}, ensure_ascii=False))
PY
