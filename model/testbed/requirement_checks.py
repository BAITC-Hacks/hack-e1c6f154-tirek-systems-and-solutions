"""Executable MH1–MH5 evidence, distinct from forecast benchmark statistics."""
from copy import deepcopy
from datetime import date,timedelta
import json
import math
import csv
from pathlib import Path

from .business import reference_calculate
from .generator import generate
from .seasonal import forecast


def deterministic_request(mode='seasonal'):
    cutoff=date(2026,2,15)
    item={'sku':'control', 'unit':'шт', 'warehouse_id':'synthetic', 'supplier_id':'synthetic-A',
          'category_raw':'C', 'launch_date':(cutoff-timedelta(days=729)).isoformat(),
          'history':[], 'events':[], 'known_promotions':[], 'analogue_history':[]}
    def demand(day):
        offset=(day-cutoff).days
        if mode=='growth': return 50*math.exp(.002*offset)
        if mode=='stable': return 20.
        return 50*(1+.55*math.sin(2*math.pi*day.toordinal()/365.25))*math.exp(.0008*offset)
    for offset in range(-729,1):
        day=cutoff+timedelta(days=offset)
        item['history'].append({'date':day.isoformat(),'observed_quantity':demand(day),
                                'availability_fraction':1.,'complete':True})
    request={'schema_version':'forecast-input-v2','as_of':cutoff.isoformat(),'horizon_days':28,'items':[item]}
    return request,[demand(cutoff+timedelta(days=d)) for d in range(1,29)]


def order_context(request, predictions):
    """Deterministic integration control only: not calibrated uncertainty."""
    fixtures=json.loads(Path(__file__).with_name('fixtures').joinpath('business_cases.json').read_text())['cases']
    template=deepcopy(next(c['input'] for c in fixtures if c['id']=='stock_base'))
    base=template['items'][0]
    template['as_of']=request['as_of']
    template['items']=[]
    for item in request['items']:
        row=deepcopy(base)
        row.update(sku=item['sku'], supplier_id=item['supplier_id'], on_hand=0, reserved=0,
                   daily_mean=predictions[item['sku']],
                   horizon_distribution=[{'quantity':sum(predictions[item['sku']]),'probability':1.}],
                   inbound=[], material_requirements=[], min_order_qty=1, order_multiple=1, unit_cost=None)
        template['items'].append(row)
    return template


def run_checks():
    cases=[]
    def record(code,name,passed,evidence):
        cases.append({'requirement':code,'case':name,'passed':bool(passed),'evidence':evidence})
    for mode in ('seasonal','growth'):
        request,truth=deterministic_request(mode)
        pred=forecast(request)['control']
        flat=sum(r['observed_quantity'] for r in request['items'][0]['history'][-56:])/56*28
        error=abs(sum(pred)-sum(truth))/sum(truth)
        record('MH2',mode, error<.1 and abs(sum(pred)-sum(truth))<abs(flat-sum(truth)),
               {'deterministic_control_horizon_error':error,'forecast':sum(pred),'truth':sum(truth),
                'mean56':flat,'daily_min':min(pred),'daily_max':max(pred)})
    request,_=deterministic_request('stable')
    for row in request['items'][0]['history'][-42:]:
        row.update(observed_quantity=0.,availability_fraction=0.)
    naive=deepcopy(request)
    for row in naive['items'][0]['history']:row['availability_fraction']=1.
    compensated=sum(forecast(request)['control']); raw=sum(forecast(naive)['control'])
    record('MH3','known_stockout',compensated>raw,{'compensated':compensated,'raw_sales_forecast':raw})
    pair={}
    for scenario in ('stable','single_project','split_project'):
        request=generate(scenario,101,'2026-04-01').observed
        pred=forecast(request)
        calculated=reference_calculate(order_context(request,pred))
        pair[scenario]={'forecast':{k:sum(v) for k,v in pred.items()},
                        'quantity':{i['sku']:i['quantity'] for i in calculated['items']}}
    for scenario in ('single_project','split_project'):
        checks=[]
        for sku,p in pair['stable']['forecast'].items():
            q=pair['stable']['quantity'][sku]
            checks.append(abs(pair[scenario]['forecast'][sku]-p)<=.1*p+1e-9 and
                          abs(pair[scenario]['quantity'][sku]-q)<=max(.1*q,1))
        record('MH4',scenario,all(checks),{'base':pair['stable'],'perturbed':pair[scenario]})
    fixtures=json.loads(Path(__file__).with_name('fixtures').joinpath('business_cases.json').read_text())['cases']
    base=deepcopy(next(c['input'] for c in fixtures if c['id']=='stock_base'))
    result=reference_calculate(base)
    base_q=result['items'][0]['quantity']
    changes={
        'stock':{'on_hand':100},
        'inbound':{'inbound':[{'quantity':100,'expected_at':'2026-04-05','status':'confirmed'}]},
        'growth':{'growth_adjustments':[{'id':'control-growth','rate':.2,'valid_from':'2026-04-02','valid_to':'2026-04-29'}]},
        'forecast':{'daily_mean':[20.]*28,'horizon_distribution':[{'quantity':560,'probability':1.}]},
    }
    for source,change in changes.items():
        request=deepcopy(base);request['items'][0].update(change)
        changed=reference_calculate(request)['items'][0]['quantity']
        record('MH1',source,changed!=base_q,{'base_quantity':base_q,'changed_quantity':changed})
    request=deepcopy(base)
    request['items'][0]['horizon_distribution']=[{'quantity':100,'probability':.6},{'quantity':400,'probability':.4}]
    category=request['items'][0]['category_raw']
    request['category_policies'][category]['target_quantile']=.5
    request['category_policies'][category]['minimum_target_quantile']=None
    qlow=reference_calculate(request)['items'][0]['quantity']
    request['category_policies'][category]['target_quantile']=.9
    qhigh=reference_calculate(request)['items'][0]['quantity']
    record('MH1','category_policy',qlow<qhigh,{'q50_quantity':qlow,'q90_quantity':qhigh})
    request=generate('stable',101,'2026-04-01').observed
    response=reference_calculate(order_context(request,forecast(request)))
    groups=response['supplier_groups']
    explanations=[i['reason'] for i in response['items']]
    record('MH5','forecast_to_two_supplier_orders',len(groups)==2 and len(set(explanations))>1 and all(
        isinstance(t,str) and any(c.isdigit() for c in t) for t in explanations),
        {'supplier_groups':groups,'items':response['items'],
         'scope':'Deterministic point-distribution integration probe; no calibrated uncertainty or automatic order dispatch.'})
    return {'mode':'synthetic_requirement_controls','forecast_accuracy':None,
            'passed':sum(c['passed'] for c in cases),'total':len(cases),'cases':cases,
            'meaning':'Selected MH1–MH5 functional controls; pass count is not forecast accuracy or full product acceptance.'}


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',required=True)
    args=parser.parse_args();result=run_checks()
    out=Path(args.output);out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    example=next(c['evidence']['items'] for c in result['cases'] if c['case']=='forecast_to_two_supplier_orders')
    columns=['mode','supplier_id','sku','quantity','unit','status','urgency','reason','requires_manual_approval']
    with out.with_name('example-orders.csv').open('w',newline='',encoding='utf-8') as handle:
        writer=csv.DictWriter(handle,fieldnames=columns,lineterminator='\n');writer.writeheader()
        for item in sorted(example,key=lambda i:(i['supplier_id'],i['sku'])):
            writer.writerow({'mode':'synthetic_reference', 'requires_manual_approval':True,
                             **{k:item[k] for k in columns if k in item}})
    print(f"Requirement controls: {result['passed']}/{result['total']} (separate from forecasting accuracy)")
    raise SystemExit(0 if result['passed']==result['total'] else 2)
