#!/usr/bin/env bash
# Exit 0 means reproducible audit evidence, NOT candidate acceptance.
set -euo pipefail
REVIEW_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$(git -C "$REVIEW_DIR" rev-parse --show-toplevel)"
export PYTHONDONTWRITEBYTECODE=1
mkdir -p "$REVIEW_DIR/.tmp"
export TMPDIR="$REVIEW_DIR/.tmp"
for review_part in root generator metrics business; do
  echo "Running $review_part audit"
  bash "$REVIEW_DIR/$review_part/run.sh" > "$REVIEW_DIR/$review_part/driver.log" 2>&1 || {
    cat "$REVIEW_DIR/$review_part/driver.log"
    exit 1
  }
done
python3 - "$REVIEW_DIR" <<'PY'
from collections import Counter
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from model.testbed.run import DEFAULT_ADAPTER, DEFAULT_CALCULATOR, verify_manifest

out = Path(sys.argv[1])
data = {part: json.loads((out/part/'results.json').read_text())
        for part in ('root', 'generator', 'metrics', 'business')}
r, g, m, b = (data[part] for part in ('root', 'generator', 'metrics', 'business'))
freeze = verify_manifest(Path('model/testbed/freeze.json'), DEFAULT_ADAPTER, DEFAULT_CALCULATOR)
assert r['audit_execution_success'] and g['audit_execution_success']
assert m['checks_failed'] == 0 and not m['oracle_calls_production_aggregate']
assert b['production_calculator_tested'] is False
assert b['forecast_accuracy']['evaluated_here'] is False
assert r['final_threshold_exit_code_correct']
assert all(s['byte_identical'] for s in r['replays'].values())
for split, replay in r['replays'].items():
    for key in ('wape_realized', 'mae_realized', 'bias_realized'):
        assert abs(replay['forecast_quality'][key] - m['summaries'][split]['overall'][key]) < 1e-10

titles = {
    'GEN-01': 'ID раскрывает исторический класс покупки',
    'GEN-02': 'Неполная проверка горизонта, полноты и акций',
    'GEN-03': 'Порядок датированных строк меняет окно baseline',
    'MET-001': 'Переполнение прерывает оценку до записи отчёта',
    'BIZ-01': 'Checker пропускает неверную схему и противоречивые ответы',
    'BIZ-02': 'Float перед Decimal добавляет лишнюю партию',
    'BIZ-03': 'Float блокирует точно достаточный бюджет',
    'BIZ-04': 'Позднее поступление скрывает дефицит после ETA заказа',
    'BIZ-05': 'Принимаются отрицательные количества, цены и сроки',
    'BIZ-06': 'Теряется источник экономических параметров',
    'BIZ-07': 'Не определено значение количества при неизвестном ограничении',
    'BIZ-08': 'Некорректные даты попадают в суммы, но исчезают из календаря',
    'BIZ-09': 'Разные допуски вероятностей обнуляют целевой запас при q=1',
}
new_ids = {'GEN-03', 'BIZ-08', 'BIZ-09'}
findings = []
for part in ('generator', 'metrics', 'business'):
    for f in data[part]['findings']:
        findings.append({'id': f['id'], 'severity': f['severity'], 'title': titles[f['id']],
                         'classification': 'ambiguity' if f['id'] == 'BIZ-07' else 'confirmed_defect',
                         'new_in_this_review': f['id'] in new_ids, 'status': 'open',
                         'evidence': part+'/results.json', 'report': part+'/report.md'})
confirmed = [f for f in findings if f['classification'] == 'confirmed_defect']
assert len(confirmed) == 12 and len(findings) == 13
evidence = {str(p.relative_to(out)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(out.rglob('*'))
            if p.is_file() and p.suffix in {'.sh', '.json', '.md', '.log'}
            and p.parent != out and '.tmp' not in p.parts}
summary = {
    'audit_date': '2026-09-23', 'audit_status': 'completed_with_open_findings',
    'audited_base_commit': 'c3dbd52b662e7b2c7cd2b65709b4e1ade64c71da',
    'git_commit_at_run': subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip(),
    'frozen_candidate_commit': freeze['source_commit'],
    'manifest_sha256': r['manifest_sha256'], 'python_version': r['python_version'],
    'frozen_source_unchanged': True,
    'team': {'subagents_started': 3, 'subagents_completed': 2,
             'completed_roles': ['independent_metrics', 'independent_business'],
             'generator_role': 'root',
             'generator_subagent_failure': 'API credit limit; two attempts, no completed work',
             'integration_and_full_replay': 'root'},
    'forecast_quality': {s: x['forecast_quality'] for s,x in r['replays'].items()},
    'test_evidence': {
        'original_unit_tests': r['existing_unit_tests'],
        'original_business_checks': {s:x['business_checks'] for s,x in r['replays'].items()},
        'paired_checks': {s:x['paired_checks'] for s,x in r['replays'].items()},
        'independent_metrics_assertions': {'passed':m['checks_passed'], 'failed':m['checks_failed']},
        'generator_development_cases': g['development_cases'],
        'business_probe_groups': b['group_counts'], 'business_counts': b['counts'],
        'meaning': 'Counts describe selected checks; neither forecasting accuracy nor a production reliability estimate.'},
    'reproducibility': {'both_reports_byte_identical': True, 'final_require_target_exit': 3,
                        'evidence_sha256': evidence},
    'findings': findings,
    'confirmed_findings': len(confirmed), 'ambiguities': 1,
    'severity_counts_confirmed': dict(Counter(f['severity'] for f in confirmed)),
    'new_confirmed_ids': sorted(new_ids),
    'limitations': [
        'Synthetic realized latent regular demand, not observed partner sales; no forecast_v2 evaluation.',
        'Previously viewed final seeds, not a new independent holdout.',
        'Original business fixtures exercise reference_only, not a production calculator.',
        'Adversarial probe frequencies are not representative production failure rates.',
        'Historical class leakage remains; baseline ID invariance does not validate arbitrary adapters.',
        'Public subprocess transport is not an OS security sandbox.',
        'Two independent subagent audits completed; generator checked by root after infrastructure failure.',
        'Frozen source, protocol, fixtures, manifest and previous reports were not changed.',
    ],
}
(out/'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False)+'\n')

lines = ['# Повторный аудит testbed — 23 сентября 2026', '',
    '**Аудит завершён; кандидат содержит открытые дефекты. Порог WAPE ≤10% не достигнут.**', '',
    'Все изменения ограничены `model/testbed/`. Замороженные Python-файлы, protocol, fixtures, freeze и прежние отчёты сохранены без изменений. Исправления модели и расчётного модуля в этот аудит не входят.', '',
    '## Участники и независимость', '',
    'Запущены три подагента. Два завершили независимые проверки метрик и бизнес-сценариев. Подагент генератора дважды завершился из-за API credit limit до выполнения работы; его задачу выполнил root. Root изучил доказательства, сопоставил находки и лично запустил все четыре набора испытаний. Три завершённых независимых аудита не заявляются.', '',
    'База worktree: `'+summary['audited_base_commit']+'`. Замороженный кандидат: `'+freeze['source_commit']+'`. Python '+r['python_version']+'. SHA-256 freeze: `'+r['manifest_sha256']+'`.', '',
    '## Качество прогноза — отдельное измерение', '',
    'Цель — реализованный **синтетический скрытый регулярный спрос**, включая потерянный при отсутствии товара, без разовых проектов. Это не наблюдаемые продажи партнёра и не оценка forecast_v2. Final seeds уже использовались: повтор не является новым независимым тестом.', '',
    'Для каждого SKU суммируются прогноз и цель за 28 дней: WAPE = Σ|P−Y| / ΣY; MAE = Σ|P−Y| / N; смещение = Σ(P−Y) / ΣY. MAE указан в штуках на SKU за горизонт. Нулевые цели не исключены. Дневные ошибки показаны отдельно, чтобы компенсация внутри горизонта была видна.', '',
    '| Выборка | Строк | WAPE | MAE, шт. | Смещение | Дневной WAPE |',
    '|---|---:|---:|---:|---:|---:|']
for split, x in r['replays'].items():
    q = x['forecast_quality']
    lines.append(f"| {split} | {q['rows']} | {q['wape_realized']:.6%} | {q['mae_realized']:.6f} | {q['bias_realized']:.6%} | {q['daily_wape_realized']:.6%} |")
lines += ['', 'WAPE по математическому ожиданию final: **24,690867%**; это дополнительная цель, не замена реализации. Ожидание условно на скрытых будущих шоках. Основной final WAPE **25,848838%** подтверждён независимой арифметикой подагента и Decimal-пересчётом root. `--require-target` вернул ожидаемый код **3**.', '',
    '### Final по сценариям', '',
    '| Сценарий | WAPE | MAE, шт. | Смещение |', '|---|---:|---:|---:|']
for scenario,q in m['summaries']['final']['groups']['scenario'].items():
    lines.append(f"| {scenario} | {q['wape_realized']:.4%} | {q['mae_realized']:.4f} | {q['bias_realized']:.4%} |")
lines += ['', 'Группы по SKU, seed, origin, scenario × seed × origin и полный пересчёт находятся в [metrics/results.json](metrics/results.json). Редкий спрос, неанонсированные скачки и combined остаются основными трудными сценариями; их не исключали из общей метрики. Общий WAPE зависит от объёмов и состава сценариев, а парные случаи разделяют траектории.', '',
    '## Результаты испытаний — не точность прогноза', '',
    '- Исходные unit tests: **25/25**. Бизнес-фикстуры: **54/54** в каждом split, только `reference_only`.',
    '- Парные MH4: **48/48** development и **64/64** final. Это узкие проверки устойчивости к проектным покупкам.',
    '- Независимые метрики: **2076** выполненных assertions; сверены **672** строки и **18 816** дневных значений, сохранённые CSV и группировки.',
    '- Генератор: **72** development-кейса, **288** SKU; проверены временные границы, цензурирование, сохранение количеств, парные траектории, транспорт и детерминизм.',
    '- Ручной бизнес-реестр quantity/status: **54/54**; net/gross метаморфные контроли: **4/4**.', '',
    '| Дополнительные бизнес-пробы | Всего | Совпало с oracle | Не совпало |',
    '|---|---:|---:|---:|']
for group in ('independent_boundary_probes','decimal_batch_sweep','decimal_money_sweep'):
    c=b['group_counts'][group]
    lines.append(f"| {group} | {c['checks']} | {c['matches']} | {c['mismatches']} |")
lines += ['', 'Две boundary-пробы относятся к неоднозначности BIZ-07. Остальные несовпадения воспроизводят дефекты. Во всех **6** кампаниях мутации ответов checker есть ложные PASS; четыре кампании сохраняют 54/54 при недопустимых ответах. Эти специально подобранные пробы не дают оценки доли ошибок в эксплуатации. Общий «процент качества» из их счётчиков не рассчитывается.', '',
    '## Находки и решения root', '',
    '**12 подтверждённых категорий: 5 high, 7 medium.** Ещё одна неоднозначность low. Новые категории относительно прежнего аудита: GEN-03, BIZ-08, BIZ-09. Повторные числовые границы и два пути переполнения не считаются отдельными дефектами.', '',
    '| ID | Важность | Вывод | Статус |', '|---|---|---|---|']
for f in findings:
    status = 'неоднозначность' if f['classification']=='ambiguity' else ('новый, открыт' if f['new_in_this_review'] else 'подтверждён, открыт')
    lines.append(f"| [{f['id']}]({f['report']}) | {f['severity']} | {f['title']} | {status} |")
lines += ['',
    'GEN-01: из ID точно восстановлены **29 211** единиц исторических проектных продаж без ложных срабатываний. Это утечка исторической классификации, не прямой доступ к будущему спросу. Замена ID на непрозрачные не меняет baseline. GEN-03: обратный порядок тех же датированных наблюдений меняет прогноз **280 → 28**; исходные сгенерированные данные отсортированы, поэтому опубликованные метрики остаются верными.', '',
    'MET-001: как переполнение одного горизонта, так и суммы конечных горизонтов приводит к OverflowError до сохранения отчёта. Обычные частичный/полный отказы корректно сохраняют все строки, null WAPE и код 2.', '',
    'BIZ-04 проверен независимой дневной траекторией **с включением предложенного заказа в день ETA**: остаётся 90 единиц неудовлетворённого спроса после ETA. Сам first_deficit_day без учёта заказа не объявляется ошибкой. BIZ-08: дата `2026-04-05x` уменьшает заказ **210 → 110**, хотя не создаёт дневного поступления. BIZ-09: вероятность `0.9999999999` проходит допуск суммы, но при q=1 даёт target=0 вместо 280; допустимы нормализация либо явный отказ, а не разрешённый нулевой заказ.', '',
    'Приоритет следующей версии: общий валидатор схемы/инвариантов checker; непрозрачные ID; строгие даты и распределения; календарная проверка покрытия поставками. Затем единая Decimal-арифметика, валидация чисел/порядка истории и сохранение отказов при переполнении. Менять эти компоненты следует в новом кандидате с новым freeze; просмотренные seeds не становятся новым holdout.', '',
    '## Воспроизведение и доказательства', '',
    'Из корня worktree, Python **3.14.4**, внешние пакеты не нужны:', '',
    '```sh', 'bash model/testbed/audits/review-2/run.sh', '```', '',
    'Команда последовательно повторяет исходные тесты и оба split, аудит генератора, независимые метрики и бизнес-пробы. Результаты и логи пишутся в эту папку. Код 0 означает, что аудит воспроизведён и доказательства сохранены, включая открытые дефекты. Он не означает приёмку кандидата.', '',
    'Для отдельных частей запустите `run.sh` внутри одной из папок `root/`, `generator/`, `metrics/` или `business/` через bash.', '',
    'Все три файла каждого исходного split (`report.json`, `rows.csv`, `report.md`) после повторного запуска совпали побайтно. Хеши файлов, отдельные показатели и реестр находок: [summary.json](summary.json). Подробности: [root](root/results.json), [генератор](generator/report.md), [метрики](metrics/report.md), [бизнес](business/report.md).', '',
    '## Ограничения', '',
    'Производственный калькулятор, HTTP/approval/revision, БД и ingestion-idempotency не проверялись. Синтетические доступность, коэффициенты акций и клиенты не заменяют реальные сведения. Subprocess с публичным JSON не является OS sandbox. Два независимых подагента завершили работу; генератор проверен root после инфраструктурного сбоя третьего. Доказательства не подтверждают 10% WAPE на реальных продажах или готовность к эксплуатации.', '']
(out/'REPORT.md').write_text('\n'.join(lines))
print(json.dumps({'audit_status': summary['audit_status'],
                  'final_wape': summary['forecast_quality']['final']['wape_realized'],
                  'confirmed_findings': len(confirmed), 'ambiguities': 1,
                  'frozen_source_unchanged': True}, ensure_ascii=False))
PY
