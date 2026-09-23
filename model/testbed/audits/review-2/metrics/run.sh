#!/bin/sh
set -eu
audit_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$audit_dir/../../../../.."
PYTHONDONTWRITEBYTECODE=1 "${PYTHON:-python3}" - <<'PY'
"""Independent arithmetic/date oracle; production aggregate is only a test subject."""
import contextlib
import copy
import csv
import hashlib
import io
import itertools
import json
import math
import platform
import tempfile
from argparse import Namespace
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from model.testbed import run
from model.testbed.adapters import call_external, validate_prediction
from model.testbed.generator import PROTOCOL, generate
from model.testbed.metrics import aggregate

ROOT = Path('model/testbed')
OUT = ROOT / 'audits/review-2/metrics'
OUT.mkdir(parents=True, exist_ok=True)
checks = []

def check(label, condition):
    if not condition:
        raise AssertionError(label)
    checks.append(label)

def same(a, b):
    if isinstance(b, dict):
        return isinstance(a, dict) and a.keys() == b.keys() and all(same(a[k], v) for k, v in b.items())
    if isinstance(b, list):
        return isinstance(a, list) and len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
    if isinstance(b, float):
        return isinstance(a, (int, float)) and math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-8)
    return a == b

def oracle(rows, horizon=28):
    """Independent formulas, including metrics absent from the original reports."""
    count = len(rows)
    failed = sum(r['error'] is not None for r in rows)
    result = dict(rows=count, failed_rows=failed,
                  realized_regular_quantity=math.fsum(r['realized_regular_quantity'] for r in rows),
                  expected_regular_quantity=math.fsum(r['expected_regular_quantity'] for r in rows),
                  project_quantity=math.fsum(r['project_quantity'] for r in rows),
                  zero_realized_rows=sum(r['realized_regular_quantity'] == 0 for r in rows))
    for label, target, daily in [('realized', 'realized_regular_quantity', 'daily_absolute_error'),
                                 ('expectation', 'expected_regular_quantity', 'daily_absolute_error_expectation')]:
        y = result[target]
        absolute = None if failed else math.fsum(abs(r['forecast_quantity'] - r[target]) for r in rows)
        signed = None if failed else math.fsum(r['forecast_quantity'] - r[target] for r in rows)
        daily_absolute = None if failed else math.fsum(r[daily] for r in rows)
        result['absolute_error_' + label] = absolute
        result['wape_' + label] = absolute / y if absolute is not None and y else None
        result['bias_' + label] = signed / y if signed is not None and y else None
        result['daily_wape_' + label] = daily_absolute / y if daily_absolute is not None and y else None
        result['mae_' + label] = absolute / count if absolute is not None and count else None
        result['mean_signed_error_' + label] = signed / count if signed is not None and count else None
        result['daily_mae_' + label] = daily_absolute / (count * horizon) if daily_absolute is not None and count else None
        result['daily_absolute_error_' + label] = daily_absolute
    return result

def production_projection(value):
    return {k: v for k, v in value.items()
            if not k.startswith(('mae_', 'mean_signed_error_', 'daily_mae_', 'daily_absolute_error_'))}

def grouping(rows, fields):
    keys = sorted({tuple(str(r[f]) for f in fields) for r in rows})
    return {'/'.join(k): oracle([r for r in rows if tuple(str(r[f]) for f in fields) == k]) for k in keys}

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

manifest = json.loads((ROOT / 'freeze.json').read_text())
frozen_files = {str(p.relative_to(ROOT)): sha(p) for p in sorted(ROOT.rglob('*.py')) +
                [ROOT / 'protocol.json', ROOT / 'fixtures/business_cases.json']}
check('frozen source hashes', frozen_files == manifest['files'])
check('Python version frozen', platform.python_version() == manifest['python_version'])
summaries = {}
for split in ('development', 'final'):
    folder = ROOT / 'reports' / split
    report = json.loads((folder / 'report.json').read_text())
    rows = report['rows']
    seeds = PROTOCOL[split + '_seeds']
    keys = set(itertools.product(seeds, PROTOCOL['scenarios'], PROTOCOL['origins'],
                                 [f'SKU-{i+1:03d}' for i in range(PROTOCOL['items_per_case'])]))
    indexed = {(r['seed'], r['scenario'], r['origin'], r['sku']): r for r in rows}
    check(split + ' complete unique coverage', set(indexed) == keys and len(indexed) == len(rows))
    check(split + ' frozen report source hashes', report['source_hashes'] == frozen_files)
    for seed, scenario, origin in itertools.product(seeds, PROTOCOL['scenarios'], PROTOCOL['origins']):
        case = generate(scenario, seed, origin)
        forecasts = call_external(report['adapter'], case.observed)
        input_hash = hashlib.sha256(json.dumps(case.observed, sort_keys=True).encode()).hexdigest()
        cutoff = date.fromisoformat(origin)
        future_dates = [(cutoff + timedelta(days=d)).isoformat() for d in range(1, 29)]
        for item in case.observed['items']:
            sku = item['sku']
            truth = case.truth[sku]
            # Date join is independent of the evaluator's positional [-28:] slice.
            realized_by_date = dict(zip(truth['dates'], truth['regular_realized'], strict=True))
            expectation_by_date = dict(zip(truth['dates'], truth['regular_expectation'], strict=True))
            project_by_date = dict(zip(case.projects[sku]['dates'], case.projects[sku]['one_off_quantity'], strict=True))
            check(f'{split}/{seed}/{scenario}/{origin}/{sku} unique truth dates', len(realized_by_date) == len(truth['dates']))
            y = [realized_by_date[d] for d in future_dates]
            mu = [expectation_by_date[d] for d in future_dates]
            forecast = forecasts[sku]
            row = indexed[seed, scenario, origin, sku]
            replay = dict(row, forecast_quantity=math.fsum(forecast),
                          realized_regular_quantity=math.fsum(y), expected_regular_quantity=math.fsum(mu),
                          project_quantity=math.fsum(project_by_date[d] for d in future_dates),
                          daily_absolute_error=math.fsum(abs(p-v) for p, v in zip(forecast, y, strict=True)),
                          daily_absolute_error_expectation=math.fsum(abs(p-v) for p, v in zip(forecast, mu, strict=True)),
                          input_sha256=input_hash, error=None)
            check(f'{split}/{seed}/{scenario}/{origin}/{sku} date-based daily replay', same(row, replay))
            check(f'{split}/{seed}/{scenario}/{origin}/{sku} triangle inequality',
                  abs(row['forecast_quantity'] - row['realized_regular_quantity']) <= row['daily_absolute_error'] + 1e-8)
    overall = oracle(rows)
    check(split + ' independent overall arithmetic', same(report['overall'], production_projection(overall)))
    independent_groups = {field: grouping(rows, [field]) for field in ('seed', 'scenario', 'origin', 'unit')}
    independent_groups['scenario_seed'] = grouping(rows, ['scenario', 'seed'])
    for field, groups in independent_groups.items():
        check(split + ' independent group ' + field,
              same(report['groups'][field], {k: production_projection(v) for k, v in groups.items()}))
    independent_groups['scenario_seed_origin'] = grouping(rows, ['scenario', 'seed', 'origin'])
    independent_groups['sku'] = grouping(rows, ['sku'])
    with (folder / 'rows.csv').open(newline='') as handle:
        csv_rows = list(csv.DictReader(handle))
    check(split + ' CSV exact rows', csv_rows == [{k: '' if v is None else str(v) for k, v in r.items()} for r in rows])
    check(split + ' target flag only primary WAPE', report['forecast_target_met'] == (overall['wape_realized'] <= .1))
    check(split + ' business count internally consistent',
          report['business']['passed'] == sum(c['passed'] for c in report['business']['cases']) and
          report['business']['total'] == len(report['business']['cases']))
    check(split + ' paired check count internally consistent',
          report['paired_project_checks']['passed'] == sum(c['passed'] for c in report['paired_project_checks']['cases']))
    zero_rows = [r for r in rows if r['realized_regular_quantity'] == 0]
    scenario_weights = {k: v['realized_regular_quantity'] / overall['realized_regular_quantity']
                        for k, v in independent_groups['scenario'].items()}
    summaries[split] = dict(overall=overall, groups=independent_groups,
                           date_join_replayed_rows=len(rows), daily_predictions_replayed=len(rows)*28,
                           zero_target_rows=zero_rows,
                           zero_target_absolute_error=math.fsum(r['forecast_quantity'] for r in zero_rows),
                           scenario_demand_weights=scenario_weights,
                           unweighted_scenario_mean_wape_diagnostic=math.fsum(g['wape_realized'] for g in independent_groups['scenario'].values()) / len(PROTOCOL['scenarios']),
                           business=dict(passed=report['business']['passed'], total=report['business']['total'],
                                         integration_status=report['business']['integration_status']),
                           paired_checks=dict(passed=report['paired_project_checks']['passed'], total=report['paired_project_checks']['total']),
                           report_hashes={p.name: sha(p) for p in (folder/'report.json', folder/'report.md', folder/'rows.csv')})
    print(f'{split}: independent replay and arithmetic verified for {len(rows)} rows', flush=True)

def row(p, y, mu=None, daily=0, project=0, error=None):
    return dict(forecast_quantity=p, realized_regular_quantity=y, expected_regular_quantity=y if mu is None else mu,
                daily_absolute_error=daily, daily_absolute_error_expectation=daily, project_quantity=project, error=error)

boundaries = {
    'weighted_with_zero_target': [row(80, 100, 80), row(30, 0, 30)],
    'zero_denominator_nonzero_prediction': [row(10, 0)],
    'all_zero': [row(0, 0)], 'empty': [],
    'missing_forecast': [row(100, 100), row(None, 200, error='missing')],
    'zero_expectation_positive_realized': [row(7, 5, 0)],
    'zero_realized_positive_expectation': [row(7, 0, 5)],
    'daily_error_without_horizon_error': [row(20, 20, daily=20)],
    'bias_cancellation_without_absolute_cancellation': [row(120, 100, daily=20), row(80, 100, daily=20)],
}
for label, rows in boundaries.items():
    check('metric boundary ' + label, same(aggregate(rows), production_projection(oracle(rows))))
check('zero-target forecast remains in numerator', oracle(boundaries['weighted_with_zero_target'])['wape_realized'] == .5)
check('project amount excluded', oracle([row(80, 100, project=0)])['wape_realized'] == oracle([row(80, 100, project=10**30)])['wape_realized'])

request = {'items': [{'sku': 'A'}], 'horizon_days': 28}
invalid = {'missing_sku': {}, 'extra_sku': {'A': [0]*28, 'B': [0]*28},
           'short': {'A': [0]*27}, 'long': {'A': [0]*29}, 'tuple': {'A': (0,)*28}}
for label, value in [('negative', -1), ('nan', float('nan')), ('infinity', float('inf')),
                     ('boolean', True), ('string', '1'), ('none', None)]:
    invalid[label] = {'A': [value]*28}
for label, value in invalid.items():
    try:
        validate_prediction(request, value)
    except ValueError:
        check('reject invalid prediction ' + label, True)
    else:
        check('reject invalid prediction ' + label, False)

# Full evaluate fault probes, isolated to this audit's directory and development seed.
# Protocol changes exist only in memory and are restored after each experiment.
small = {'development_seeds': [101], 'scenarios': ['stable', 'single_project', 'split_project'],
         'origins': ['2025-07-01']}

def evaluate_probe(name, callback):
    with tempfile.TemporaryDirectory(prefix='.probe-', dir=OUT) as folder:
        args = Namespace(split='development', adapter=run.DEFAULT_ADAPTER, calculator=run.DEFAULT_CALCULATOR,
                         timeout=2, output=folder, require_target=False)
        error = None
        code = None
        with patch.dict(PROTOCOL, small), patch.object(run, 'call_external', side_effect=callback), \
             patch.object(run, 'check_cases', return_value=[]), contextlib.redirect_stdout(io.StringIO()), \
             contextlib.redirect_stderr(io.StringIO()):
            try:
                code = run.evaluate(args)
            except Exception as exc:
                error = {'type': type(exc).__name__, 'message': str(exc)}
        target = Path(folder) / 'report.json'
        saved = json.loads(target.read_text()) if target.exists() else None
        return dict(name=name, exit_code=code, exception=error, report_written=saved is not None,
                    rows=saved['rows'] if saved else None, overall=saved['overall'] if saved else None,
                    forecast_target_met=saved['forecast_target_met'] if saved else None)

def missing(spec, payload, timeout):
    return validate_prediction(payload, {})

mixed_calls = 0
def partial_missing(spec, payload, timeout):
    global mixed_calls
    mixed_calls += 1
    prediction = {i['sku']: [1]*payload['horizon_days'] for i in payload['items']} if mixed_calls != 2 else {}
    return validate_prediction(payload, prediction)

probes = {}
for label, callback, expected_failed in [('all_missing', missing, 12), ('one_case_missing', partial_missing, 4)]:
    probe = evaluate_probe(label, callback)
    check(label + ' all rows retained and failure code', probe['report_written'] and probe['exit_code'] == 2 and
          probe['overall']['rows'] == 12 and probe['overall']['failed_rows'] == expected_failed)
    check(label + ' no misleading partial metric', probe['overall']['wape_realized'] is None and
          probe['overall']['wape_expectation'] is None and not probe['forecast_target_met'])
    check(label + ' truth denominator retained', probe['overall']['realized_regular_quantity'] > 0)
    probes[label] = probe

for label, value in [('overflow_horizon', 1e308), ('overflow_across_rows', 1e306)]:
    def huge(spec, payload, timeout, value=value):
        return validate_prediction(payload, {i['sku']: [value]*payload['horizon_days'] for i in payload['items']})
    probe = evaluate_probe(label, huge)
    probe.update(daily_value=value, individually_valid_daily=True, horizon_sum_finite=math.isfinite(sum([value]*28)))
    check(label + ' known failure retained as finding', not probe['report_written'] and
          probe['exception'] is not None and probe['exception']['type'] == 'OverflowError')
    probes[label] = probe
check('cross-row overflow occurs with finite individual horizons', probes['overflow_across_rows']['horizon_sum_finite'])

findings = [dict(id='MET-001', severity='medium', status='reproduced_not_fixed',
                 problem='Individually finite nonnegative daily values can overflow a horizon or the aggregate across finite horizons. evaluate raises OverflowError before saving a report, violating its retained-failure policy.',
                 existing_evidence=probes['overflow_horizon'], additional_evidence=probes['overflow_across_rows'],
                 locations=['model/testbed/adapters.py:50', 'model/testbed/run.py:117', 'model/testbed/metrics.py:15', 'model/testbed/run.py:87'],
                 scope='Robustness defect for numeric blow-ups in an external adapter; stored baseline results are finite and unaffected.',
                 recommendation='For the next candidate, validate finite horizon/error totals within protected handling, preserve failed rows, and guard cross-row accumulation. Keep this frozen candidate unchanged.')]

prior = json.loads((ROOT / 'audits/2026-09-23/metrics.json').read_text())
for split in summaries:
    check(split + ' prior audit arithmetic independently reproduced',
          same(prior['summaries'][split]['independent_overall'], production_projection(summaries[split]['overall'])))
check('frozen files unchanged after audit', frozen_files == run.file_hashes())

result = dict(audit='independent-metrics-review-2', mode='synthetic', date='2026-09-23',
              frozen_source_commit=manifest['source_commit'], manifest_sha256=sha(ROOT/'freeze.json'),
              python_version=platform.python_version(), frozen_sources_unchanged=True,
              oracle_calls_production_aggregate=False, checks_passed=len(checks), checks_failed=0,
              audit_exit_code_meaning='0 means audit executed and evidence recorded, not that the candidate has no defects.',
              checks=checks, summaries=summaries, boundary_cases={k: oracle(v) for k,v in boundaries.items()},
              invalid_prediction_classes_rejected=list(invalid), failure_probes=probes, findings=findings,
              limitations=['Already-viewed final seeds: repeated correctness/reproducibility audit, not a new independent test.',
                           'Synthetic hidden regular demand is not observed partner sales and does not establish forecast_v2 accuracy.',
                           'Date joins and arithmetic are independent; generator and baseline are replayed, not independently reimplemented.',
                           'Conditional expectation includes generator state for unannounced shocks and is not a cutoff-information forecast oracle.',
                           'Scenario weighting follows this protocol and paired cases share paths; no statistical confidence claim is made.',
                           'Business pass rate and audit assertion count are separate from forecast accuracy.'])
(OUT/'results.json').write_text(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False)+'\n')

def pct(value):
    return 'undefined' if value is None else f'{value*100:.6f}%'

lines = ['# Независимая проверка метрик — review 2', '',
         '**Сохранённые метрики верны; WAPE ≤10% не достигнут.** Final seeds уже просмотрены: это повторная проверка, не новый независимый тест.', '',
         'Все 672 строки (288 development + 384 final), 18 816 дневных прогнозов и скрытых целей повторены через публичный subprocess-контракт. Цели выбраны join по конкретным датам origin+1 … origin+28, независимо от среза оценщика. Числа сверены с CSV и всеми штатными группами. В results.json дополнительно сохранены группы scenario × seed × origin и SKU.', '',
         'Oracle не вызывает production aggregate: WAPE = Σ|P−Y|/ΣY, MAE = Σ|P−Y|/N, bias = Σ(P−Y)/ΣY. MAE имеет размерность штук на 28-дневный SKU-прогноз; дневной MAE делит сумму дневных ошибок на N×28. Production aggregate вызывается только как объект граничных испытаний. Относительный допуск 1e−12, абсолютный 1e−8.', '',
         '| Выборка | Строк | WAPE реализация | MAE реализация | Bias реализация | WAPE ожидание | MAE ожидание |',
         '|---|---:|---:|---:|---:|---:|---:|']
for split, value in summaries.items():
    v = value['overall']
    lines.append(f"| {split} | {v['rows']} | {pct(v['wape_realized'])} | {v['mae_realized']:.6f} | {pct(v['bias_realized'])} | {pct(v['wape_expectation'])} | {v['mae_expectation']:.6f} |")
lines += ['', '| Выборка | Дневной WAPE реализация | Дневной MAE реализация | Дневной WAPE ожидание | Дневной MAE ожидание |', '|---|---:|---:|---:|---:|']
for split, value in summaries.items():
    v = value['overall']
    lines.append(f"| {split} | {pct(v['daily_wape_realized'])} | {v['daily_mae_realized']:.6f} | {pct(v['daily_wape_expectation'])} | {v['daily_mae_expectation']:.6f} |")
lines += ['', '## Final по сценариям', '', '| Сценарий | WAPE реализация | MAE реализация | Bias реализация | WAPE ожидание |', '|---|---:|---:|---:|---:|']
for scenario, v in summaries['final']['groups']['scenario'].items():
    lines.append(f"| {scenario} | {pct(v['wape_realized'])} | {v['mae_realized']:.6f} | {pct(v['bias_realized'])} | {pct(v['wape_expectation'])} |")
lines += ['', '## Отказы и границы', '',
          'Полнота декартова произведения seed × scenario × origin × SKU подтверждена, дубликатов и отброшенных строк нет. Все прогнозные строки — шт. Две нулевые цели development и три final сохранены; их прогнозы входят в абсолютную ошибку. Нулевой знаменатель даёт null, не 0; абсолютная ошибка сохраняется. Ошибка за горизонт не подменяется дневной: суммы внутри 28 дней могут взаимно компенсироваться.', '',
          '11 классов невалидных прогнозов отклоняются. Полный evaluate при полном отказе (12/12 строк) и частичном (4/12) сохраняет все строки и истинные знаменатели, записывает null для общего WAPE и возвращает код 2. Проектный объём не входит в цель регулярного спроса. Смещение не подменяет абсолютную ошибку.', '',
          '**MET-001, medium, подтверждено без исправления:** 28 допустимых конечных значений 1e308 дают бесконечную сумму и OverflowError до записи отчёта. Новая дополнительная проба: 1e306 в день даёт конечные горизонты 2.8e307, но сумма ошибок 12 строк переполняет math.fsum в aggregate. В обоих случаях report.json не создаётся. Защищать нужно и суммы горизонтов, и межстрочную агрегацию. Это один корневой дефект с расширенным доказательством, а не два независимых дефекта; исходные baseline-метрики он не меняет.', '',
          'Новых ложных чисел в сохранённых отчётах не найдено. Штатные отчёты не показывают MAE; независимые MAE добавлены здесь и в JSON без изменения замороженного кода.', '',
          '## Отдельные показатели и ограничения', '',
          'Бизнес-примеры: 54/54 для reference_only в каждом split. Парные MH4: 48/48 development, 64/64 final. Это отдельные проверки, а не проценты точности прогноза. Независимая проверка бизнес-правил выполняется другим аудитором; здесь проверена только согласованность счётчиков.', '',
          'Цель — синтетический скрытый регулярный спрос. Она не является партнёрскими наблюдаемыми продажами; эти результаты ничего не доказывают о forecast_v2 на реальных данных. Математическое ожидание содержит скрытые будущие шоки. Общий WAPE зависит от фиксированных весов сценариев; парные сценарии используют общие траектории, поэтому строки не являются независимой случайной выборкой.', '',
          '## Воспроизведение', '', '```sh', 'sh model/testbed/audits/review-2/metrics/run.sh', '```', '',
          f'Python {platform.python_version()}, frozen source `{manifest["source_commit"]}`. Скрипт пишет только в свою папку, временные пробы удаляет, .py/протокол/fixtures/freeze не меняет. Код 0 означает завершение аудита с сохранением доказательств, включая известный дефект. Проверки аудита не являются точностью прогноза. Все формулы, результаты {len(checks)} assertions, группировки и исключения — в [results.json](results.json).', '']
(OUT/'report.md').write_text('\n'.join(lines))
print(json.dumps({'checks_passed': len(checks), 'findings': len(findings),
                  'final': summaries['final']['overall']}, ensure_ascii=False))
PY
