"""Independent audit regression evidence and Decimal benchmark reconciliation."""
from copy import deepcopy
from decimal import Decimal
import csv
import json
from pathlib import Path

from .adapters import validate_request,validate_prediction
from .baseline import forecast as old_baseline
from .business import check_cases,reference_calculate
from .generator import generate
from .metrics import aggregate

ROOT=Path(__file__).resolve().parent


def evidence():
    cases=json.loads((ROOT/'fixtures/business_cases.json').read_text())['cases']
    inputs={c['id']:c['input'] for c in cases}
    results={}
    def record(identifier,passed,details):
        if not passed: raise AssertionError((identifier,details))
        results[identifier]={'status':'regression_confirmed','evidence':details}
    request=generate('split_project',101,'2026-04-01').observed
    import re
    ids=[e['event_id'] for i in request['items'] for e in i['events']]
    record('GEN-01',all(re.fullmatch(r'event-[0-9a-f]{32}',v) for v in ids),
           {'event_count':len(ids),'identifier_format':'opaque 128-bit, independent RNG, no class suffix',
            'limit':'Not an OS sandbox or protection from a malicious adapter knowing the generator seeds.'})
    rejected=[]
    for name in ('horizon_negative','horizon_bool','horizon_fraction','complete_string','promo_zero','promo_invalid_date'):
        changed=generate('known_promotion',101,'2026-04-01').observed
        if name=='horizon_negative':changed['horizon_days']=-1
        if name=='horizon_bool':changed['horizon_days']=True
        if name=='horizon_fraction':changed['horizon_days']=1.5
        if name=='complete_string':changed['items'][0]['history'][0]['complete']='false'
        if name=='promo_zero':changed['items'][0]['known_promotions'][0]['planned_multiplier']=0
        if name=='promo_invalid_date':changed['items'][0]['known_promotions'][0]['start_date']='2026-03-01x'
        try:validate_request(changed)
        except ValueError:rejected.append(name)
    record('GEN-02',len(rejected)==6,{'invalid_requests_rejected':rejected})
    normal=old_baseline(request)
    reversed_request=deepcopy(request)
    for item in reversed_request['items']:item['history'].reverse()
    rejected_order=False
    try:validate_request(reversed_request)
    except ValueError:rejected_order=True
    record('GEN-03',rejected_order and normal==old_baseline(reversed_request),
           {'boundary_rejects_unsorted':rejected_order,'direct_baseline_sorts_by_date':True})
    overflow_rejected=False
    try:validate_prediction({'horizon_days':28,'items':[{'sku':'x'}]},{'x':[1e308]*28})
    except ValueError:overflow_rejected=True
    rows=[{'unit':'шт','forecast_quantity':2.8e307,'realized_regular_quantity':1,'expected_regular_quantity':1,
           'project_quantity':0,'daily_absolute_error':2.8e307,'daily_absolute_error_expectation':2.8e307,'error':None} for _ in range(12)]
    overflow=aggregate(rows)
    record('MET-001',overflow_rejected and overflow['wape_realized'] is None and overflow['failed_rows']==12,
           {'horizon_overflow_rejected':overflow_rejected,'aggregate_overflow':overflow,
            'full_evaluate_proofs':'tests/test_boundary_corrections.py::test_full_evaluate_retains_both_overflow_paths'})
    mutation_results=[]
    for mutation in ('status','rules','supplier','reason','negative_quantity','unit'):
        altered=set()
        def mutate(r):
            out=reference_calculate(r);before=deepcopy(out)
            for i in out['items']:
                if mutation=='status' and i['status']=='ok':i['status']='needs_data'
                if mutation=='rules':i['rules']=' '.join(i['rules'])
                if mutation=='supplier':i['supplier_id']='wrong-supplier'
                if mutation=='reason':i['reason']=True
                if mutation=='negative_quantity' and i['quantity'] is not None:i['quantity']=-1
                if mutation=='unit' and i['quantity'] is not None:i['unit']='wrong-unit'
            if out!=before:
                altered.add(json.dumps(r,sort_keys=True))
            return out
        checked=check_cases(mutate)
        false_pass=[c['id'] for c in checked if c['passed'] and json.dumps(inputs[c['id']],sort_keys=True) in altered]
        mutation_results.append({'mutation':mutation,'changed_unique_inputs':len(altered),'false_pass_ids':false_pass})
    record('BIZ-01',all(not c['false_pass_ids'] for c in mutation_results),{'campaigns':mutation_results})
    batch=[];money=[]
    for tenth in range(1,21):
        r=deepcopy(inputs['stock_base']);need=Decimal(tenth)/10
        r['items'][0].update(unit='м',order_unit='м',unit_quantum=.01,on_hand=float(Decimal(280)-need),min_order_qty=.1,order_multiple=.1)
        actual=reference_calculate(r)['items'][0]['quantity']
        batch.append({'need':float(need),'actual':actual,'match':Decimal(str(actual))==need})
    for quantity in range(1,13):
        for cents in (1,3,7,10):
            r=deepcopy(inputs['stock_base']);price=Decimal(cents)/100;budget=quantity*price
            r['items'][0].update(on_hand=280-quantity,unit_cost=float(price));r['budget_kzt']=float(budget)
            out=reference_calculate(r)
            money.append({'quantity':quantity,'price':float(price),'budget':float(budget),'match':not out['approval_blocked'] and Decimal(str(out['total_cost_kzt']))==budget})
    record('BIZ-02',all(r['match'] for r in batch),{'decimal_boundaries':batch})
    record('BIZ-03',all(r['match'] for r in money),{'equal_budget_boundaries':money})
    row=reference_calculate(deepcopy(inputs['inbound_last_day']))['items'][0]
    record('BIZ-04',row['residual_unmet_quantity']==90 and row['residual_deficit_day']==19 and row['requires_expedite'],
           {k:row[k] for k in ('quantity','residual_deficit_day','residual_unmet_quantity','requires_expedite','reason')})
    invalid=[]
    for change in ({'daily_mean':[-1]*28},{'unit_cost':-1},{'lead_time_days':-1,'review_period_days':29},
                   {'inbound':[{'quantity':-20,'expected_at':'2026-04-05','status':'confirmed'}]}):
        r=deepcopy(inputs['stock_base']);r['items'][0].update(change);out=reference_calculate(r)
        invalid.append(out['items'][0]['status']=='needs_data' and out['items'][0]['quantity'] is None and out['approval_blocked'])
    record('BIZ-05',all(invalid),{'invalid_domain_cases_blocked':len(invalid)})
    r=deepcopy(inputs['stock_base']);r['items'][0]['economics']={'underage_cost':1,'overage_cost':1,'horizon_days':28,'source':'audit-owner-approved-v3'}
    source=reference_calculate(r)['items'][0]['economics_source']
    record('BIZ-06',source=='audit-owner-approved-v3',{'preserved_economics_source':source})
    r=deepcopy(inputs['stock_base']);r['items'][0].update(on_hand=69,min_order_qty=None,order_multiple=5)
    out=reference_calculate(r);row=out['items'][0]
    record('BIZ-07',row['quantity']==211 and row['quantity_kind']=='provisional_base_need' and out['approval_blocked'],
           {'resolution':'Explicit provisional base need, not a feasible supplier lot; approval blocked.',
            'quantity':row['quantity'],'quantity_kind':row['quantity_kind'],'constraints':row['order_constraints_base']})
    dates=[]
    for field,entry in (('inbound',{'quantity':100,'expected_at':'2026-04-05x','status':'confirmed'}),
                        ('material_requirements',{'quantity':100,'needed_at':'2026-04-02x','already_accounted_quantity':0})):
        r=deepcopy(inputs['stock_base']);r['items'][0][field]=[entry];out=reference_calculate(r)
        dates.append({'field':field,'status':out['items'][0]['status'],'quantity':out['items'][0]['quantity'],'blocked':out['approval_blocked']})
    record('BIZ-08',all(v['status']=='needs_data' and v['quantity'] is None and v['blocked'] for v in dates),{'invalid_dates':dates})
    r=deepcopy(inputs['stock_base']);r['items'][0].update(horizon_distribution=[{'quantity':280,'probability':.9999999999}],
        economics={'underage_cost':1,'overage_cost':0,'horizon_days':28,'source':'audit'})
    row=reference_calculate(r)['items'][0]
    record('BIZ-09',row['target_stock']==280 and row['quantity']==210,{'target_stock':row['target_stock'],'quantity':row['quantity'],'target_quantile':row['target_quantile']})
    return results


def reconciliation():
    result={}
    for split in ('development','final'):
        report=json.loads((ROOT/'reports'/('corrected-'+split)/'report.json').read_text())
        def oracle(rows):
            total=sum(Decimal(str(r['realized_regular_quantity'])) for r in rows)
            absolute=sum(abs(Decimal(str(r['forecast_quantity']))-Decimal(str(r['realized_regular_quantity']))) for r in rows)
            signed=sum(Decimal(str(r['forecast_quantity']))-Decimal(str(r['realized_regular_quantity'])) for r in rows)
            return {'wape':float(absolute/total),'mae':float(absolute/len(rows)),'bias':float(signed/total)}
        groups=[('overall',report['rows'],report['overall'])]
        for field in ('scenario','seed','origin','sku','unit'):
            for value,metric in report['groups'][field].items():
                groups.append((field+'/'+value,[r for r in report['rows'] if str(r[field])==value],metric))
        for name,rows,metric in groups:
            independent=oracle(rows)
            for short,long in (('wape','wape_realized'),('mae','mae_realized'),('bias','bias_realized')):
                assert abs(independent[short]-metric[long])<1e-9,(split,name,short)
        # Shared latent paths allow exact target equality despite repaired public IDs.
        old=json.loads((ROOT/'reports'/split/'report.json').read_text())
        key=lambda r:(r['seed'],r['scenario'],r['origin'],r['sku'])
        assert {key(r):r['realized_regular_quantity'] for r in old['rows']}=={key(r):r['realized_regular_quantity'] for r in report['rows']}
        with (ROOT/'reports'/('corrected-'+split)/'rows.csv').open() as handle:
            saved=list(csv.DictReader(handle))
        assert len(saved)==len(report['rows'])
        for original,row in zip(report['rows'],saved):
            for field in ('forecast_quantity','realized_regular_quantity','expected_regular_quantity'):
                assert float(row[field])==original[field]
        result[split]={'independent_decimal':oracle(report['rows']),'groups_reconciled':len(groups),
                       'same_realized_targets_as_v2':True,'rows':len(report['rows']),
                       'csv_verified':True,
                       'old_wape':old['overall']['wape_realized'],'new_wape':report['overall']['wape_realized']}
    return result


if __name__=='__main__':
    out=ROOT/'audits/corrections/regression-evidence.json'
    data={'findings':evidence(),'forecast_metrics_separate':reconciliation(),
          'status':'12 prior defects covered by regression probes; BIZ-07 contract ambiguity explicitly resolved',
          'scope':'Synthetic/local reference only; no production or real-data accuracy claim.'}
    out.write_text(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    print('Audit regressions:',len(data['findings']),'records; metrics independently reconciled.')
