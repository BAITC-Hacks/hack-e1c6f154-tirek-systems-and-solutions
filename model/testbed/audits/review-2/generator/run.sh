#!/usr/bin/env bash
set -euo pipefail
GEN_REVIEW="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$(git -C "$GEN_REVIEW" rev-parse --show-toplevel)"
export PYTHONDONTWRITEBYTECODE=1
python3 - "$GEN_REVIEW" <<'PY'
from collections import defaultdict, Counter
from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path
import hashlib
import json
import math
import statistics
import sys
from unittest.mock import patch
from model.testbed.adapters import call_external, validate_request
from model.testbed.baseline import forecast
from model.testbed.generator import PROTOCOL, generate, rng_for, poisson
from model.testbed.run import verify_manifest, DEFAULT_ADAPTER, DEFAULT_CALCULATOR
out=Path(sys.argv[1])
verify_manifest('model/testbed/freeze.json',DEFAULT_ADAPTER,DEFAULT_CALCULATOR)
checks=Counter(); leakage=Counter(); cases={}; moments=[]

def check(value,kind):
    checks[kind]+=1
    assert value, kind

for seed in PROTOCOL['development_seeds']:
    for origin in PROTOCOL['origins']:
        for scenario in PROTOCOL['scenarios']:
            case=generate(scenario,seed,origin);cases[seed,origin,scenario]=case
            validate_request(case.observed)
            check(case==generate(scenario,seed,origin),'deterministic')
            modified=deepcopy(case)
            for ledger in modified.truth.values():
                for name in ('regular_realized','regular_expectation'):
                    for i, stamp in enumerate(ledger['dates']):
                        if stamp>origin: ledger[name][i]=1e12
            modified.projects.clear()
            check(modified.observed==case.observed,'private_truth_mutation_no_public_change')
            opaque=deepcopy(case.observed)
            for item in opaque['items']:
                for j,event in enumerate(item['events']):
                    event['event_id']=hashlib.sha256(f"opaque-{item['sku']}-{j}".encode()).hexdigest()
            check(forecast(opaque)==forecast(case.observed),'baseline_opaque_id_invariance')
            for index,item in enumerate(case.observed['items']):
                sku=item['sku']; truth=case.truth[sku]; proj=case.projects[sku]
                future=[i for i,d in enumerate(truth['dates']) if d>origin]
                check(len(future)==28 and truth['dates'][future[0]]==(date.fromisoformat(origin)+timedelta(days=1)).isoformat(),'exact_future_dates')
                check(len(truth['dates'])==len(truth['regular_realized'])==len(truth['regular_expectation'])==588,'ledger_lengths')
                byday=defaultdict(list)
                for event in item['events']:
                    check(item['launch_date']<=event['date']<=origin,'events_point_in_time')
                    byday[event['date']].append(event)
                for p in item['known_promotions']:
                    check(p['announced_at']<=origin,'promotion_known_at_cutoff')
                for row in item['analogue_history']:check(row['date']<=origin,'analogue_point_in_time')
                t=dict(zip(truth['dates'],truth['regular_realized']));p=dict(zip(proj['dates'],proj['one_off_quantity']))
                rng=rng_for(seed,origin,index,'availability')
                for row in item['history']:
                    stamp=row['date'];a=row['availability_fraction']
                    d, project=t[stamp],p[stamp]
                    sold_d=d if a==1 else sum(rng.random()<a for _ in range(d))
                    sold_p=project if a==1 else sum(rng.random()<a for _ in range(project))
                    check(row['observed_quantity']==sold_d+sold_p,'independent_censor_reconstruction')
                    check(row['observed_quantity']==sum(e['quantity'] for e in byday[stamp]),'documents_reconcile')
                    check(item['launch_date']<=stamp<=origin,'history_point_in_time')
                    decoded=sum(e['quantity'] for e in byday[stamp] if int(e['event_id'].rsplit('-',1)[1])>=2)
                    leakage['decoded_project_units']+=decoded
                    leakage['true_sold_project_units']+=sold_p
                    leakage['false_positive_units']+=max(0,decoded-sold_p)
                    leakage['false_negative_units']+=max(0,sold_p-decoded)
                if scenario=='intermittent':
                    expected=.08*(8,18,40,90)[index]*2
                    check(all(x==expected for x in truth['regular_expectation']),'intermittent_analytic_expectation')
        base=cases[seed,origin,'stable']
        for scenario in ('single_project','split_project','full_stockout','partial_stockout'):
            check(cases[seed,origin,scenario].truth==base.truth,'paired_latent_paths')
        for sku in base.truth:
            check(sum(cases[seed,origin,'single_project'].projects[sku]['one_off_quantity'])==sum(cases[seed,origin,'split_project'].projects[sku]['one_off_quantity']),'same_project_total')
for mean in (0,.1,1,20,70):
    rng=rng_for(101,'review2-moments',mean);v=[poisson(rng,mean) for _ in range(10000)]
    m=statistics.mean(v);variance=statistics.variance(v)
    z=(m-mean)/math.sqrt(mean/len(v)) if mean else 0
    check(abs(z)<6,'poisson_mean_sanity')
    moments.append({'mean':mean,'sample_mean':m,'sample_variance':variance,'mean_z':z,'n':len(v)})

source=cases[101,PROTOCOL['origins'][0],'known_promotion'].observed
boundary=[]
for mutation in ('future_history','future_announcement','private_truth','negative_horizon','boolean_horizon','fractional_horizon','string_complete','reversed_promotion','invalid_promotion_date','zero_promotion'):
    r=deepcopy(source);item=r['items'][0]
    if mutation=='future_history':item['history'][0]['date']='2099-01-01'
    if mutation=='future_announcement':item['known_promotions'][0]['announced_at']='2099-01-01'
    if mutation=='private_truth':r['truth']={}
    if mutation=='negative_horizon':r['horizon_days']=-1
    if mutation=='boolean_horizon':r['horizon_days']=True
    if mutation=='fractional_horizon':r['horizon_days']=1.5
    if mutation=='string_complete':item['history'][0]['complete']='false'
    if mutation=='reversed_promotion':item['known_promotions'][0]['end_date']='1900-01-01'
    if mutation=='invalid_promotion_date':item['known_promotions'][0]['start_date']='broken'
    if mutation=='zero_promotion':item['known_promotions'][0]['planned_multiplier']=0
    try:validate_request(r);accepted=True
    except (ValueError,TypeError):accepted=False
    error=None
    if accepted:
        try:forecast(r)
        except Exception as exc:error=type(exc).__name__+': '+str(exc)
    boundary.append({'mutation':mutation,'accepted':accepted,'expected_acceptance':False,'baseline_error':error})
check(not any(r['accepted'] for r in boundary[:3]),'future_private_rejected')

# Same factual time-stamped dataset in a different import order must either be sorted or rejected.
sorted_input=deepcopy(cases[101,PROTOCOL['origins'][0],'stable'].observed)
for item in sorted_input['items']:
    for i,row in enumerate(item['history']):
        row['observed_quantity']=1. if i<len(item['history'])-56 else 10.
    item['events']=[]
reversed_input=deepcopy(sorted_input)
for item in reversed_input['items']:item['history'].reverse()
validate_request(sorted_input);validate_request(reversed_input)
normal=forecast(sorted_input);reverse=forecast(reversed_input)
order_probe={sku:{'sorted_horizon':sum(normal[sku]),'reversed_horizon':sum(reverse[sku])} for sku in normal}
check(any(normal[s]!=reverse[s] for s in normal),'confirmed_order_sensitivity')

# Inspect actual JSON serialization/argv at the process seam without substituting truth.
transport=[]
def capture(*args,**kwargs):
    transport.append({'argv':args[0],'request_keys':sorted(json.loads(kwargs['input']))})
    class R:
        returncode=0;stderr='';stdout=json.dumps(forecast(json.loads(kwargs['input'])))
    return R()
with patch('model.testbed.adapters.subprocess.run',side_effect=capture):
    call_external(DEFAULT_ADAPTER,source)
check(transport[0]['request_keys']==['as_of','horizon_days','items','schema_version'],'transport_public_keys')
check(len(transport[0]['argv'])==4 and transport[0]['argv'][-1]==DEFAULT_ADAPTER,'transport_no_seed_scenario_path')
verify_manifest('model/testbed/freeze.json',DEFAULT_ADAPTER,DEFAULT_CALCULATOR)
findings=[
 {'id':'GEN-01','prior_id':'GEN-01','severity':'high','status':'confirmed_open','title':'event_id suffix discloses historical project classes exactly','evidence':'leakage','expected':'Opaque identifier independent of hidden classification','actual':dict(leakage),'scope':'historical classification leak, not direct future demand; baseline does not read event_id'},
 {'id':'GEN-02','prior_id':'GEN-02','severity':'medium','status':'confirmed_open','title':'Public request validator accepts invalid types/ranges/dates','evidence':'boundary_probes','expected':'Reject malformed horizon/completeness/promotion inputs','actual':[x for x in boundary if x['accepted']]},
 {'id':'GEN-03','severity':'medium','status':'confirmed_open','title':'Accepted history order changes chronological window and forecast','expected':'Sort historical dates or reject unsorted input','actual':order_probe,'source':['adapters.py:validate_request','baseline.py:rows[-56:]'],'note':'Generated evaluation inputs are sorted, so saved benchmark numbers remain valid; external import ordering is not protected.'}
]
result={'author':'root (generator subagent failed twice before any work due to API credit limit)',
 'audit_execution_success':True,'audit_status':'findings_open','development_cases':len(cases),'sku_cases':4*len(cases),
 'checks':dict(checks),'leakage':dict(leakage),'boundary_probes':boundary,'history_order_probe':order_probe,
 'transport':transport,'poisson_sanity':moments,'findings':findings,'forecast_quality_measured':False,
 'limitations':['Subprocess is not an OS sandbox.','SKU suffix fixes scale across seeds.','Promotion multiplier and availability are exact synthetic quantities.','Generated days are all complete.','Expected demand conditions on private future shocks.']}
(out/'results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
(out/'report.md').write_text('''# Повторный аудит генератора и утечек

Автор: основной агент. Подагент генератора дважды завершился ошибкой лимита API до работы; его независимая проверка не заявляется.

Проверены 72 development-кейса (288 SKU), даты, баланс документов, независимое воспроизведение цензурирования, парные латентные пути, аналитическое ожидание редкого спроса и транспорт JSON. Замена скрытой будущей истины не меняет вход; замена event_id непрозрачными не меняет baseline.

Подтверждён GEN-01: суффикс event_id позволяет точно извлечь исторические проектные продажи. Это скрытая подсказка внешнему адаптеру, не прямая утечка будущего спроса. GEN-02 подтверждён и расширен: дробный горизонт и нулевой коэффициент акции принимаются, затем baseline падает с TypeError/ZeroDivisionError.

Новая находка GEN-03: история с обратным порядком дат проходит валидатор, после чего rows[-56:] выбирает древние дни. При тех же датированных фактах прогноз всех SKU меняется с 280 до 28. Требуется сортировка или отказ на границе. Генератор выдаёт отсортированную историю; сохранённые метрики эта находка не опровергает.

Счётчики, ожидания и фактические ответы — в results.json. Статистические sanity-пробы не доказывают реалистичность генератора. Результат исполнения аудита и прогнозная точность не объединяются.

Запуск из корня: `bash model/testbed/audits/review-2/generator/run.sh`.
''')
print(json.dumps({'cases':len(cases),'leakage':dict(leakage),'findings':len(findings),'history_order_probe':order_probe},ensure_ascii=False))
PY
