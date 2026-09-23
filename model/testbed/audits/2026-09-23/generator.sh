#!/bin/sh
set -eu
audit_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$audit_dir/../../../.."
export PYTHONDONTWRITEBYTECODE=1
python3 - <<'PY'
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import date, timedelta
import hashlib
import json
import math
from pathlib import Path
import statistics

from model.testbed.adapters import call_external, validate_request
from model.testbed.baseline import forecast
from model.testbed.generator import PROTOCOL, generate, poisson, rng_for
from model.testbed.run import DEFAULT_ADAPTER, DEFAULT_CALCULATOR, verify_manifest

root = Path('model/testbed')
destination = root / 'audits/2026-09-23/generator.json'
seeds = (101, 202, 303)
assert tuple(PROTOCOL['development_seeds']) == seeds
verify_manifest(root / 'freeze.json', DEFAULT_ADAPTER, DEFAULT_CALCULATOR)
checks = Counter()
violations = []
leak = Counter()
recurring = Counter()
censoring = defaultdict(lambda: [0, 0.0, 0.0])
intermittent = defaultdict(list)
examples = []
cases = {}

def check(condition, label, detail=None):
    checks[label] += 1
    if not condition:
        violations.append({'check': label, 'detail': detail})

for seed in seeds:
    for origin in PROTOCOL['origins']:
        for scenario in PROTOCOL['scenarios']:
            case = generate(scenario, seed, origin)
            cases[(seed, origin, scenario)] = case
            check(case == generate(scenario, seed, origin), 'deterministic_case')
            validate_request(case.observed)
            checks['valid_public_request'] += 1
            check(set(case.observed) == {'schema_version', 'as_of', 'horizon_days', 'items'}, 'public_top_level')
            altered = deepcopy(case.observed)
            for item in altered['items']:
                for i, event in enumerate(item['events']):
                    event['event_id'] = f'opaque-{item["sku"]}-{i}'
            check(forecast(altered) == forecast(case.observed), 'baseline_invariant_to_event_id')
            original = json.dumps(case.observed, sort_keys=True)
            private_mutation = deepcopy(case)
            for truth in private_mutation.truth.values():
                truth['regular_realized'][-28:] = [999999] * 28
                truth['regular_expectation'][-28:] = [999999] * 28
            private_mutation.projects.clear()
            check(json.dumps(private_mutation.observed, sort_keys=True) == original,
                  'private_future_mutation_does_not_change_public_input')
            for index, item in enumerate(case.observed['items']):
                sku = item['sku']
                ledger = case.truth[sku]
                project_ledger = case.projects[sku]
                check(len(ledger['dates']) == 588 and len(ledger['regular_realized']) == 588
                      and len(ledger['regular_expectation']) == 588, 'truth_dimensions')
                check(ledger['dates'] == project_ledger['dates'], 'aligned_truth_project_dates')
                check(ledger['dates'][-28] == (date.fromisoformat(origin) + timedelta(days=1)).isoformat(),
                      'future_starts_after_cutoff')
                truth_by_day = dict(zip(ledger['dates'], ledger['regular_realized']))
                project_by_day = dict(zip(project_ledger['dates'], project_ledger['one_off_quantity']))
                events_by_day = defaultdict(list)
                for event in item['events']:
                    check(item['launch_date'] <= event['date'] <= origin, 'event_time_boundary')
                    events_by_day[event['date']].append(event)
                    leak['all_observed_events'] += 1
                    if int(event['event_id'].rsplit('-', 1)[-1]) >= 2:
                        leak['events_with_project_suffix'] += 1
                        if len(examples) < 3:
                            examples.append({'seed': seed, 'origin': origin, 'scenario': scenario,
                                             'event_id': event['event_id'], 'quantity': event['quantity']})
                # Independent replay of binomial thinning using evaluator-only demand
                # and its separate availability stream yields true sold project units.
                censor_rng = rng_for(seed, origin, index, 'availability')
                for row in item['history']:
                    stamp, fraction = row['date'], row['availability_fraction']
                    demand, project = truth_by_day[stamp], project_by_day[stamp]
                    sold_regular = demand if fraction == 1 else sum(censor_rng.random() < fraction for _ in range(demand))
                    sold_project = project if fraction == 1 else sum(censor_rng.random() < fraction for _ in range(project))
                    check(row['observed_quantity'] == sold_regular + sold_project, 'independent_censor_replay')
                    check(row['observed_quantity'] == sum(e['quantity'] for e in events_by_day[stamp]), 'document_conservation')
                    check(0 <= row['observed_quantity'] <= demand + project, 'censoring_bounds')
                    check(item['launch_date'] <= stamp <= origin, 'history_time_boundary')
                    if fraction == 0:
                        check(row['observed_quantity'] == 0, 'full_stockout_zero_sales')
                    decoded = sum(e['quantity'] for e in events_by_day[stamp]
                                  if int(e['event_id'].rsplit('-', 1)[-1]) >= 2)
                    leak['history_rows'] += 1
                    leak['true_sold_project_units'] += sold_project
                    leak['decoded_project_units'] += decoded
                    leak['true_positive_units'] += min(decoded, sold_project)
                    leak['false_positive_units'] += max(decoded - sold_project, 0)
                    leak['false_negative_units'] += max(sold_project - decoded, 0)
                    leak['mismatched_days'] += decoded != sold_project
                    if fraction not in (0, 1):
                        acc = censoring[f'{scenario}/availability={fraction}']
                        acc[0] += row['observed_quantity']
                        acc[1] += (demand + project) * fraction
                        acc[2] += (demand + project) * fraction * (1-fraction)
                for name in ('analogue_history', 'known_promotions'):
                    for row in item[name]:
                        check(row.get('announced_at', row.get('date')) <= origin, f'{name}_time_boundary')
                if scenario == 'new_product':
                    check(len(item['history']) == (0 if index % 2 == 0 else 8), 'new_product_history_length')
                    check(sum(ledger['regular_expectation'][-28:]) > 0, 'new_product_nonzero_future')
                if scenario == 'intermittent':
                    intermittent[index].extend(ledger['regular_realized'])
                    check(all(v == 0.08 * (8, 18, 40, 90)[index] * 2 for v in ledger['regular_expectation']),
                          'intermittent_analytic_expectation')
                if scenario == 'recurring_client':
                    regular_rng = rng_for(seed, origin, scenario, index, 'regular')
                    base = (8, 18, 40, 90)[index]
                    for stamp in ledger['dates']:
                        day = date.fromisoformat(stamp)
                        daily = poisson(regular_rng, base * (0.95, 1.03, 1.05, 1.10, 1.07, 0.93, 0.87)[day.weekday()])
                        bulk = poisson(regular_rng, base * 9) if day.weekday() == 1 else 0
                        check(truth_by_day[stamp] == daily + bulk, 'independent_recurring_replay')
                        if stamp <= origin:
                            decoded = sum(e['quantity'] for e in events_by_day[stamp]
                                          if int(e['event_id'].rsplit('-', 1)[-1]) == 1)
                            recurring['true_recurring_units'] += bulk
                            recurring['decoded_recurring_units'] += decoded
                            recurring['false_positive_units'] += max(decoded-bulk, 0)
                            recurring['false_negative_units'] += max(bulk-decoded, 0)
                            recurring['mismatched_days'] += decoded != bulk
        stable = cases[(seed, origin, 'stable')]
        for scenario in ('full_stockout', 'partial_stockout', 'single_project', 'split_project'):
            check(stable.truth == cases[(seed, origin, scenario)].truth, 'paired_regular_truth')
        single = cases[(seed, origin, 'single_project')]
        split = cases[(seed, origin, 'split_project')]
        for sku in single.projects:
            check(sum(single.projects[sku]['one_off_quantity']) == sum(split.projects[sku]['one_off_quantity']),
                  'single_split_equal_total')

# Sanity checks, not calibrated acceptance limits or generator tuning.
poisson_moments = []
for seed in seeds:
    for mean in (0, 0.1, 1, 20, 70):
        rng = rng_for(seed, 'audit-poisson')
        values = [poisson(rng, mean) for _ in range(10000)]
        observed_mean, variance = statistics.mean(values), statistics.variance(values)
        mean_z = (observed_mean-mean)/math.sqrt(mean/len(values)) if mean else 0
        check(abs(mean_z) < 6 and (abs(variance/mean-1) < .15 if mean else variance == 0), 'poisson_moments')
        poisson_moments.append({'seed': seed, 'mean': mean, 'n': len(values), 'sample_mean': observed_mean,
                                'sample_variance': variance, 'mean_z_score': mean_z})
intermittent_moments = []
for index, values in intermittent.items():
    p, m = .08, (8, 18, 40, 90)[index] * 2
    expected, variance = p*m, p*(2-p)*m*m-p*m
    observed_mean = statistics.mean(values)
    z = (observed_mean-expected)/math.sqrt(variance/len(values))
    check(abs(z) < 6, 'intermittent_moments')
    intermittent_moments.append({'sku': f'SKU-{index+1:03d}', 'n': len(values), 'expected_mean': expected,
                                 'sample_mean': observed_mean, 'positive_frequency': sum(v > 0 for v in values)/len(values),
                                 'mean_z_score': z})
for key, (observed, expected, variance) in censoring.items():
    check(abs((observed-expected)/math.sqrt(variance)) < 6, 'censoring_mean', key)

# Validated failure cases and holes in input validation. These affect mutated
# development requests only; no candidate source or saved candidate input is changed.
boundary = []
template = cases[(101, PROTOCOL['origins'][0], 'known_promotion')].observed
for name in ('future_history', 'future_event', 'future_promotion_announcement', 'private_field',
             'negative_horizon', 'boolean_horizon', 'string_complete', 'reversed_promotion', 'invalid_promotion_date'):
    request = deepcopy(template)
    item = request['items'][0]
    if name == 'future_history': item['history'][0]['date'] = '2099-01-01'
    if name == 'future_event': item['events'][0]['date'] = '2099-01-01'
    if name == 'future_promotion_announcement': item['known_promotions'][0]['announced_at'] = '2099-01-01'
    if name == 'private_field': request['seed'] = 101
    if name == 'negative_horizon': request['horizon_days'] = -1
    if name == 'boolean_horizon': request['horizon_days'] = True
    if name == 'string_complete': item['history'][0]['complete'] = 'false'
    if name == 'reversed_promotion': item['known_promotions'][0]['end_date'] = '1900-01-01'
    if name == 'invalid_promotion_date': item['known_promotions'][0]['start_date'] = 'not-a-date'
    try:
        validate_request(request)
        accepted = True
    except (ValueError, TypeError):
        accepted = False
    boundary.append({'mutation': name, 'accepted': accepted, 'expected_acceptance': False})
    if name in ('future_history', 'future_event', 'future_promotion_announcement', 'private_field'):
        check(not accepted, 'explicit_future_and_private_rejection', name)

request = cases[(101, PROTOCOL['origins'][0], 'split_project')].observed
check(call_external(DEFAULT_ADAPTER, request) == forecast(request), 'external_process_matches_baseline')
verify_manifest(root / 'freeze.json', DEFAULT_ADAPTER, DEFAULT_CALCULATOR)
evidence = {
    'audit': 'generator and leakage; no forecast tuning', 'development_seeds': list(seeds),
    'final_seeds_generated': [], 'origins': PROTOCOL['origins'], 'cases': len(cases), 'sku_cases': len(cases)*4,
    'candidate_freeze_sha256': hashlib.sha256((root/'freeze.json').read_bytes()).hexdigest(),
    'probe_sha256': hashlib.sha256((destination.with_suffix('.sh')).read_bytes()).hexdigest(),
    'checks': dict(checks), 'unexpected_invariant_failures': violations,
    'historical_project_id_leakage': dict(leak), 'historical_recurring_id_leakage': dict(recurring),
    'examples': examples, 'input_validation_mutations': boundary,
    'poisson_moments': poisson_moments, 'intermittent_moments': intermittent_moments,
    'censoring_moments': {key: {'observed_units': v[0], 'expected_units': v[1], 'variance': v[2],
                               'z_score': (v[0]-v[1])/math.sqrt(v[2])} for key,v in censoring.items()},
    'summary': {'probe_completed': True, 'unexpected_invariant_failures': len(violations),
                'confirmed_findings': 2, 'high_findings': 1, 'medium_findings': 1,
                'forecast_quality_measured_by_this_probe': False},
    'findings': [
        {'id': 'GEN-01', 'severity': 'high', 'status': 'confirmed',
         'title': 'Observed event_id exposes historical project and recurring classifications',
         'evidence': {'source': ['generator.py:129', 'generator.py:132', 'generator.py:138'],
                      'expected': 'Opaque event identifiers without evaluator classification',
                      'actual': 'Suffix 0=normal, 1=recurring, >=2=project; exact historical-unit reconstruction',
                      'details': ['historical_project_id_leakage', 'historical_recurring_id_leakage'],
                      'not_future_demand_leakage': True}},
        {'id': 'GEN-02', 'severity': 'medium', 'status': 'confirmed',
         'title': 'Incomplete request validation',
         'evidence': {'source': ['adapters.py:9', 'adapters.py:33', 'adapters.py:36'],
                      'expected': 'Reject invalid horizon types/ranges, non-boolean complete and invalid promotion dates/ranges',
                      'actual': 'Five malformed development-request mutations accepted',
                      'details': ['input_validation_mutations']}}],
    'limitations': ['Trusted adapter process is not an OS sandbox.',
                    'SKU index fixes demand scale across seeds; synthetic identifier generalization is untested.',
                    'Expectation conditions on private shocks, not only information available at cutoff.',
                    'Complete flags are always true in generated evaluation scenarios.',
                    'No production data or inventory-demand feedback is modeled.',
                    'Findings do not imply baseline used the leaked ID; it is invariant to ID replacement in all development cases.'],
}
destination.write_text(json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)+'\n')
print(json.dumps({'evidence': str(destination), 'cases': len(cases), 'unexpected_invariant_failures': len(violations),
                  'confirmed_findings': 2, 'project_id_leakage': dict(leak), 'recurring_id_leakage': dict(recurring)}, ensure_ascii=False))
if violations:
    raise SystemExit(1)
PY
