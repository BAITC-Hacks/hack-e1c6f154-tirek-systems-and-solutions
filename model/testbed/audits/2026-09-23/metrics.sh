#!/bin/sh
set -eu
audit_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$audit_dir/../../../.."
PYTHONDONTWRITEBYTECODE=1 python3 - <<'PY'
"""Independent metric oracle; no changes to frozen code or holdout candidate."""
import contextlib
import copy
import csv
import hashlib
import io
import itertools
import json
import math
import platform
from argparse import Namespace
from datetime import date
from pathlib import Path
from unittest.mock import patch

from model.testbed.adapters import call_external, validate_prediction
from model.testbed.generator import PROTOCOL, generate
from model.testbed.metrics import aggregate  # Implementation under test, never the oracle.
from model.testbed import run

ROOT = Path('model/testbed')
OUT = ROOT / 'audits/2026-09-23'
checks = []
findings = []

def check(name, condition):
    if not condition:
        raise AssertionError(name)
    checks.append(name)

def equal(actual, expected):
    if isinstance(expected, dict):
        return (isinstance(actual, dict) and actual.keys() == expected.keys()
                and all(equal(actual[k], v) for k, v in expected.items()))
    if isinstance(expected, list):
        return isinstance(actual, list) and len(actual) == len(expected) and all(map(lambda p: equal(*p), zip(actual, expected)))
    if isinstance(expected, float):
        return isinstance(actual, (int, float)) and math.isclose(actual, expected, rel_tol=1e-12, abs_tol=1e-8)
    return actual == expected

def oracle(rows):
    """Formula specified directly, with independent stable sums and no project target."""
    nfailed = sum(r['error'] is not None for r in rows)
    y = math.fsum(r['realized_regular_quantity'] for r in rows)
    mu = math.fsum(r['expected_regular_quantity'] for r in rows)
    result = dict(rows=len(rows), failed_rows=nfailed,
                  realized_regular_quantity=y, expected_regular_quantity=mu,
                  project_quantity=math.fsum(r['project_quantity'] for r in rows),
                  zero_realized_rows=sum(r['realized_regular_quantity'] == 0 for r in rows))
    for label, field, denominator, daily in [
        ('realized', 'realized_regular_quantity', y, 'daily_absolute_error'),
        ('expectation', 'expected_regular_quantity', mu, 'daily_absolute_error_expectation')]:
        numerator = None if nfailed else math.fsum(abs(r['forecast_quantity'] - r[field]) for r in rows)
        result['absolute_error_' + label] = numerator
        result['wape_' + label] = None if nfailed or denominator == 0 else numerator / denominator
        result['bias_' + label] = None if nfailed or denominator == 0 else math.fsum(r['forecast_quantity'] - r[field] for r in rows) / denominator
        result['daily_wape_' + label] = None if nfailed or denominator == 0 else math.fsum(r[daily] for r in rows) / denominator
    return result

def pct(v):
    return 'undefined' if v is None else f'{100*v:.2f}%'

manifest = json.loads((ROOT/'freeze.json').read_text())
actual_hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in sorted(ROOT.rglob('*.py')) + [ROOT/'protocol.json', ROOT/'fixtures/business_cases.json']}
check('frozen file set and every source hash', actual_hashes == manifest['files'])
check('python version pinned', platform.python_version() == manifest['python_version'])
check('protocol pinned', PROTOCOL == manifest['protocol'])
summaries = {}
for split in ('development', 'final'):
    folder = ROOT/'reports'/split
    report = json.loads((folder/'report.json').read_text())
    rows = report['rows']
    seeds = PROTOCOL[split+'_seeds']
    expected_keys = set(itertools.product(seeds, PROTOCOL['scenarios'], PROTOCOL['origins'], [f'SKU-{i+1:03d}' for i in range(PROTOCOL['items_per_case'])]))
    keyed = {(r['seed'],r['scenario'],r['origin'],r['sku']):r for r in rows}
    check(split+' complete unique Cartesian product', len(keyed) == len(rows) and set(keyed) == expected_keys)
    check(split+' identical frozen source hashes', report['source_hashes'] == actual_hashes)
    check(split+' seeds exactly declared', report['seeds'] == seeds)
    if split == 'final':
        check('final manifest digest', report['manifest_sha256'] == hashlib.sha256((ROOT/'freeze.json').read_bytes()).hexdigest())
    daily_replayed = 0
    for seed, scenario, origin in itertools.product(seeds, PROTOCOL['scenarios'], PROTOCOL['origins']):
        case = generate(scenario, seed, origin)
        predictions = call_external(report['adapter'], case.observed)
        input_hash = hashlib.sha256(json.dumps(case.observed, sort_keys=True).encode()).hexdigest()
        for item in case.observed['items']:
            sku = item['sku']
            row = keyed[seed,scenario,origin,sku]
            truth = case.truth[sku]
            # Select by dates, independently of evaluate's negative-index slicing.
            future = [i for i, d in enumerate(truth['dates']) if date.fromisoformat(d) > date.fromisoformat(origin)]
            check(f'{split}/{seed}/{scenario}/{origin}/{sku} full horizon', len(future) == PROTOCOL['horizon_days'])
            realized = [truth['regular_realized'][i] for i in future]
            expectation = [truth['regular_expectation'][i] for i in future]
            project = [q for d,q in zip(case.projects[sku]['dates'], case.projects[sku]['one_off_quantity']) if d > origin]
            p = predictions[sku]
            expected = dict(row, forecast_quantity=math.fsum(p), realized_regular_quantity=math.fsum(realized),
                            expected_regular_quantity=math.fsum(expectation), project_quantity=math.fsum(project),
                            daily_absolute_error=math.fsum(abs(a-b) for a,b in zip(p,realized)),
                            daily_absolute_error_expectation=math.fsum(abs(a-b) for a,b in zip(p,expectation)),
                            input_sha256=input_hash, error=None)
            check(f'{split}/{seed}/{scenario}/{origin}/{sku} regenerated daily forecast and hidden truth', equal(row, expected))
            daily_replayed += 1
    independent = oracle(rows)
    check(split+' overall independent oracle', equal(report['overall'], independent))
    check(split+' target flag solely from WAPE', report['forecast_target_met'] == (independent['wape_realized'] is not None and independent['wape_realized'] <= .1))
    group_oracles = {}
    for field in ('seed','scenario','origin','unit'):
        group_oracles[field] = {str(v):oracle([r for r in rows if r[field] == v]) for v in sorted({r[field] for r in rows})}
    group_oracles['scenario_seed'] = {f'{s}/{seed}':oracle([r for r in rows if r['scenario'] == s and r['seed'] == seed]) for s,seed in itertools.product(PROTOCOL['scenarios'], seeds)}
    check(split+' every seed/scenario/cutoff/unit/scenario-seed aggregate', equal(report['groups'], group_oracles))
    with (folder/'rows.csv').open(newline='') as f:
        csv_rows = list(csv.DictReader(f))
    check(split+' CSV row count', len(csv_rows) == len(rows))
    for index,(csv_row,row) in enumerate(zip(csv_rows,rows)):
        check(f'{split} CSV row {index}', csv_row == {k:'' if v is None else str(v) for k,v in row.items()})
    md = (folder/'report.md').read_text()
    check(split+' Markdown overall realized', f'WAPE по реализованному скрытому регулярному спросу: **{pct(independent["wape_realized"])}**.' in md)
    check(split+' Markdown overall expectation', f'WAPE относительно математического ожидания: **{pct(independent["wape_expectation"])}**.' in md)
    for key,g in group_oracles['scenario_seed'].items():
        check(split+' Markdown scenario/seed '+key, f'| {key} | {g["rows"]} | {pct(g["wape_realized"])} | {pct(g["wape_expectation"])} | {g["failed_rows"]} |' in md)
    for key,g in group_oracles['seed'].items():
        check(split+' Markdown seed '+key, f'| {key} | {pct(g["wape_realized"])} | {pct(g["wape_expectation"])} |' in md)
    for key in ('business','paired_project_checks'):
        section = report[key]
        check(split+' '+key+' counts', section['total'] == len(section['cases']) and section['passed'] == sum(x['passed'] for x in section['cases']))
        check(split+' '+key+' Markdown count', f'**{section["passed"]}/{section["total"]}**' in md)
    paired_oracle = []
    for seed,origin,index,scenario in itertools.product(seeds,PROTOCOL['origins'],range(PROTOCOL['items_per_case']),('single_project','split_project')):
        sku = f'SKU-{index+1:03d}'
        base = keyed[seed,'stable',origin,sku]['forecast_quantity']
        changed = keyed[seed,scenario,origin,sku]['forecast_quantity']
        difference = abs(changed-base)
        quantity_difference = abs(math.ceil(changed)-math.ceil(base))
        paired_oracle.append({'id':f'MH4/{seed}/{origin}/{sku}/{scenario}',
                              'passed':difference <= PROTOCOL['project_forecast_tolerance_fraction']*base+1e-9 and quantity_difference <= max(PROTOCOL['project_quantity_tolerance_fraction']*math.ceil(base),PROTOCOL['project_minimum_batch']),
                              'forecast_difference':difference,'quantity_difference':quantity_difference})
    check(split+' paired MH4 independently recomputed from horizon predictions', equal(report['paired_project_checks']['cases'],paired_oracle))
    for case in report['business']['cases']:
        check(split+' business Markdown '+case['id'], f'| {case["id"]} | {", ".join(case["requirements"])} | PASS |' in md and case['passed'])
    check(split+' pass rate explicitly separated', '90% пройденных тестов не означает 90% качества прогноза.' in md and 'Business pass rate is not forecast quality.' in report['interpretation'])
    check(split+' shared unit only', set(group_oracles['unit']) == {'шт'})
    naive_mean = math.fsum(v['wape_realized'] for v in group_oracles['scenario'].values()) / len(group_oracles['scenario'])
    summaries[split] = {'independent_overall':independent, 'independent_groups':group_oracles,
                        'daily_prediction_rows_replayed':daily_replayed,
                        'unweighted_mean_scenario_wape_for_comparison_only':naive_mean,
                        'business_passed':report['business']['passed'], 'business_total':report['business']['total'],
                        'paired_project_passed':report['paired_project_checks']['passed'],
                        'paired_project_total':report['paired_project_checks']['total'],
                        'files_sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in [folder/'report.json',folder/'rows.csv',folder/'report.md']}}
    print(f'{split}: {len(rows)} rows independently replayed and all aggregates/CSV/Markdown verified', flush=True)

def row(p,y,mu=None,error=None,project=100000,daily=0):
    return dict(forecast_quantity=p, realized_regular_quantity=y, expected_regular_quantity=y if mu is None else mu,
                project_quantity=project, daily_absolute_error=daily, daily_absolute_error_expectation=daily, error=error)

boundary_rows = {
    'weighted_with_zero_target':[row(80,100,80), row(30,0,30)],
    'zero_denominator_nonzero_error':[row(10,0)],
    'both_all_zero':[row(0,0)],
    'empty':[],
    'missing_prediction_retained':[row(100,100),row(None,200,error='missing')],
    'expectation_zero_realization_positive':[row(7,5,0)],
    'realization_zero_expectation_positive':[row(7,0,5)],
    'horizon_cancellation_daily_distinct':[row(20,20,daily=20)],
}
for name, rows in boundary_rows.items():
    check('metric boundary '+name, equal(aggregate(rows),oracle(rows)))
check('zero denominator preserves absolute error', aggregate(boundary_rows['zero_denominator_nonzero_error'])['absolute_error_realized'] == 10)
check('project quantity cannot change target', oracle([row(80,100,project=0)])['wape_realized'] == oracle([row(80,100,project=10**30)])['wape_realized'] == .2)

request = {'items':[{'sku':'A'}], 'horizon_days':28}
invalid = {'missing_sku':{}, 'extra_sku':{'A':[0]*28,'B':[0]*28}, 'short':{'A':[0]*27},
           'long':{'A':[0]*29}, 'tuple':{'A':(0,)*28}}
for name,v in [('negative',-1),('nan',float('nan')),('infinity',float('inf')),('boolean',True),('string','1'),('none',None)]:
    invalid[name] = {'A':[v]*28}
for name,p in invalid.items():
    try:
        validate_prediction(request,p)
    except ValueError:
        check('invalid prediction rejected '+name,True)
    else:
        check('invalid prediction rejected '+name,False)

class CapturedReport(Exception):
    pass

captured = {}
def capture_report(path, data):
    captured.update(data)
    raise CapturedReport()

# Development-only fault injection in memory. Files and frozen protocol never change.
small_protocol = {'development_seeds':[101], 'scenarios':['stable','single_project','split_project'], 'origins':['2025-07-01']}
args = Namespace(split='development',adapter='model.testbed.tests.adapter_examples:missing_sku',
                 calculator=run.DEFAULT_CALCULATOR,timeout=2,output=str(OUT/'metrics-not-written'),require_target=False)
with patch.dict(PROTOCOL, small_protocol), patch.object(run,'check_cases',return_value=[]), patch.object(run,'dump',side_effect=capture_report), contextlib.redirect_stderr(io.StringIO()):
    try:
        run.evaluate(args)
    except CapturedReport:
        pass
check('missing adapter complete report has all 12 failed rows', captured['overall']['rows'] == captured['overall']['failed_rows'] == 12)
check('missing adapter no misleading partial WAPE', captured['overall']['wape_realized'] is None and captured['overall']['wape_expectation'] is None and not captured['forecast_target_met'])
check('missing adapter truth denominators retained', captured['overall']['realized_regular_quantity'] > 0)

def extreme_prediction(spec, request, timeout):
    return validate_prediction(request,{i['sku']:[1e308]*request['horizon_days'] for i in request['items']})

captured.clear()
overflow = None
unwritten_report = Path(args.output)/'report.json'
check('overflow destination does not exist before probe', not unwritten_report.exists())
with patch.dict(PROTOCOL, small_protocol), patch.object(run,'check_cases',return_value=[]), patch.object(run,'call_external',side_effect=extreme_prediction), patch.object(run,'dump',side_effect=capture_report), contextlib.redirect_stderr(io.StringIO()):
    try:
        run.evaluate(args)
    except Exception as exc:
        overflow = {'type':type(exc).__name__,'message':str(exc)}
check('confirmed finite daily values overflow horizon and abort before report', overflow and overflow['type'] == 'OverflowError' and not captured and not unwritten_report.exists())
findings.append({'id':'MET-001','severity':'medium','status':'confirmed_not_fixed',
                 'locations':['model/testbed/adapters.py:50','model/testbed/run.py:117','model/testbed/run.py:87'],
                 'problem':'All 28 daily predictions can be finite and individually valid while their sum overflows to infinity. evaluate then aborts in MH4 ceil(inf), before writing any report; the failure-retention contract does not cover this numeric failure.',
                 'evidence':{'daily_value':1e308,'days':28,'validator_accepts':True,'sum_finite':False,'evaluation_exception':overflow,'report_written':False,'development_only':True},
                 'impact':'External model numeric blow-up loses the complete run and its difficult/failed rows. No effect on the stored finite baseline scores.',
                 'recommendation':'For a future candidate, reject nonfinite horizon/error aggregates inside the protected per-case path, or use bounded quantities and safe accumulation; convert the failure to retained null metric rows. Do not change this already-exposed holdout candidate.'})

historical = json.loads((ROOT/'reports/verification.json').read_text())
check('historical final hashes match actual files', historical['files_sha256'] == summaries['final']['files_sha256'])
check('historical row count', historical['forecast_rows'] == summaries['final']['independent_overall']['rows'])
check('standalone business report matches final business report', json.loads((ROOT/'reports/business.json').read_text())['cases'] == json.loads((ROOT/'reports/final/report.json').read_text())['business']['cases'])
result_text = (ROOT/'RESULTS.md').read_text()
readme_text = (ROOT/'README.md').read_text()
for key in ('wape_realized','wape_expectation'):
    expected = pct(summaries['final']['independent_overall'][key])
    check('RESULTS overall '+key,expected in result_text)
    check('README overall '+key,expected in readme_text)
for group in summaries['final']['independent_groups']['scenario'].values():
    check('RESULTS scenario realized '+pct(group['wape_realized']), pct(group['wape_realized']) in result_text)
    check('RESULTS scenario expectation '+pct(group['wape_expectation']), pct(group['wape_expectation']) in result_text)
check('final and development targets both missed', all(s['independent_overall']['wape_realized'] > .1 for s in summaries.values()))

payload = {'audit':'independent-metrics-v1','date':'2026-09-23','python_version':platform.python_version(),
           'scope':'Frozen v2.0 reports; independent arithmetic and date-based daily replay; development-only fault injection.',
           'oracle_uses_production_aggregate':False,'numeric_comparison':{'relative_tolerance':1e-12,'absolute_tolerance':1e-8},
           'frozen_source_commit':manifest['source_commit'],'manifest_sha256':hashlib.sha256((ROOT/'freeze.json').read_bytes()).hexdigest(),
           'frozen_sources_unchanged':True,'checks_passed':len(checks),'checks_failed':0,'checks':checks,
           'summaries':summaries,'boundary_cases':{k:oracle(v) for k,v in boundary_rows.items()},
           'invalid_prediction_shapes_rejected':list(invalid),'findings':findings,
           'limitations':['Baseline/generator are replayed unchanged, not independently reimplemented; correctness of generator distributions belongs to the separate generator audit.',
                          'Assertion count measures audit execution, never forecast accuracy or statistical confidence.',
                          'Forecast horizon WAPE, daily WAPE, conditional-expectation error and business pass counts are different measures.',
                          'Seeds are already exposed. This audit is reproducibility and correctness analysis, not a fresh holdout or model selection.',
                          'Paired scenarios share regular paths; their pooled metric intentionally reflects protocol weights and is not a population performance estimate.',
                          'The confirmed overflow defect is recorded without modifying frozen code.']}
(OUT/'metrics.json').write_text(json.dumps(payload,indent=2,ensure_ascii=False,allow_nan=False)+'\n')
print(json.dumps({'audit_assertions_passed':len(checks),'findings':len(findings),'final_independent_overall':summaries['final']['independent_overall']},ensure_ascii=False))
PY
