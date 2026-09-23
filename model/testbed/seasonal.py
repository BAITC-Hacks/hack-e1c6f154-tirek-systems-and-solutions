"""Past-only local selection of means and a seasonal/trend regression.

No latent demand, scenario, seed, future inventory or category labels are read.
Hyperparameters and three inner 28-day windows are fixed before evaluation.
This is a synthetic testbed adapter, not a claim of partner-data accuracy.
"""
from collections import defaultdict
from datetime import date, timedelta
import math
import statistics

METHODS = ('mean56', 'mean168', 'seasonal_trend')


def promotion(item, stamp):
    return math.prod(p['planned_multiplier'] for p in item['known_promotions']
                     if p['start_date'] <= stamp <= p['end_date'])


def at_cutoff(item, cutoff):
    """Rebuild each validation training input using its own information date."""
    stamp=cutoff.isoformat()
    result=dict(item)
    for field in ('history','events','analogue_history'):
        result[field]=[r for r in item[field] if r['date'] <= stamp]
    result['known_promotions']=[p for p in item['known_promotions'] if p['announced_at'] <= stamp]
    return result


def observations(item):
    rows=sorted(item['history'],key=lambda r:r['date'])
    usable=[r for r in rows if r['complete'] and r['availability_fraction']>0]
    typical=statistics.median(r['observed_quantity']/r['availability_fraction'] for r in usable) if usable else 0
    # Sparse positive purchases are not automatically one-off projects. A client
    # heuristic needs a nonzero ordinary daily baseline to justify an exclusion.
    excluded=defaultdict(float)
    if typical > 0:
        clients=defaultdict(list)
        for e in item['events']:
            clients[e['client_id']].append(e)
        for events in clients.values():
            if len({date.fromisoformat(e['date']).toordinal()//7 for e in events})>=3:
                continue
            ordered=sorted(events,key=lambda e:e['date'])
            while ordered:
                start=date.fromisoformat(ordered[0]['date'])
                group=[e for e in ordered if (date.fromisoformat(e['date'])-start).days<7]
                ordered=ordered[len(group):]
                if sum(e['quantity'] for e in group)>8*typical:
                    for e in group:
                        excluded[e['date']]+=e['quantity']
    return [(date.fromisoformat(r['date']),
             max(0,r['observed_quantity']-excluded[r['date']])/promotion(item,r['date']),
             r['availability_fraction']) for r in usable]


def solve(matrix, target):
    """Small pivoted linear system, no external numerical dependency."""
    n=len(target)
    a=[row[:]+[value] for row,value in zip(matrix,target)]
    for col in range(n):
        pivot=max(range(col,n),key=lambda row:abs(a[row][col]))
        a[col],a[pivot]=a[pivot],a[col]
        if abs(a[col][col])<1e-12:
            raise ValueError('Singular seasonal fit')
        scale=a[col][col]
        a[col]=[v/scale for v in a[col]]
        for row in range(n):
            if row==col: continue
            factor=a[row][col]
            a[row]=[v-factor*w for v,w in zip(a[row],a[col])]
    return [row[-1] for row in a]


def features(day, cutoff):
    phase=2*math.pi*day.toordinal()/365.25
    return [1,(day-cutoff).days/365.25,math.sin(phase),math.cos(phase),math.sin(2*phase),math.cos(2*phase)]


def fitted(item, cutoff, method):
    obs=observations(item)
    days=56 if method=='mean56' else 168
    recent=[r for r in obs if (cutoff-r[0]).days<days]
    rate=sum(r[1] for r in recent)/sum(r[2] for r in recent) if recent else 0
    if len(item['history'])<28 and item['analogue_history']:
        analogue=statistics.mean(r['quantity'] for r in item['analogue_history'])
        weight=min(1,len(item['history'])/28)
        rate=weight*rate+(1-weight)*analogue
    flat=lambda day: rate*promotion(item,day.isoformat())
    if method!='seasonal_trend' or len(obs)<280 or (obs[-1][0]-obs[0][0]).days<365:
        return flat
    if sum(r[1]>0 for r in obs)/len(obs)<.4:
        return flat
    bins=defaultdict(list)
    for r in obs:
        bins[(cutoff-r[0]).days//7].append(r)
    training=[]
    for block,rows in sorted(bins.items()):
        exposure=sum(r[2] for r in rows)
        if exposure<3: continue
        midpoint=cutoff-timedelta(days=7*block+3)
        training.append((features(midpoint,cutoff),math.log(sum(r[1] for r in rows)/exposure+.25),exposure))
    if len(training)<45: return flat
    matrix=[[sum(w*x[i]*x[j] for x,y,w in training)+(1e-3 if i==j and i else 0)
             for j in range(6)] for i in range(6)]
    target=[sum(w*x[i]*y for x,y,w in training) for i in range(6)]
    coefficients=solve(matrix,target)
    def curve(day):
        log_rate=sum(a*b for a,b in zip(coefficients,features(day,cutoff)))
        return max(0,math.exp(max(-30,min(30,log_rate)))-.25)
    # Calibrate residual mean and weekday factors using only this training set.
    expected=sum(curve(day)*exposure for day,y,exposure in obs)
    calibration=sum(y for day,y,exposure in obs)/expected if expected else 1
    by_weekday=defaultdict(lambda:[0.,0.])
    for day,y,exposure in obs:
        by_weekday[day.weekday()][0]+=y
        by_weekday[day.weekday()][1]+=curve(day)*calibration*exposure
    factors={k:(a+10)/(b+10) for k,(a,b) in by_weekday.items()}
    return lambda day: curve(day)*calibration*factors.get(day.weekday(),1)*promotion(item,day.isoformat())


def select(item, cutoff):
    scores={method:0. for method in METHODS}
    windows=0
    for lag in (84,56,28):
        origin=cutoff-timedelta(days=lag)
        train=at_cutoff(item,origin)
        if len(train['history'])<168: continue
        held=[r for r in item['history'] if origin.isoformat()<r['date']<=(origin+timedelta(days=28)).isoformat()
              and r['complete'] and r['availability_fraction']>0]
        if sum(r['availability_fraction'] for r in held)<14: continue
        actual=sum(r['observed_quantity'] for r in held)
        for method in METHODS:
            model=fitted(train,origin,method)
            predicted=sum(model(date.fromisoformat(r['date']))*r['availability_fraction'] for r in held)
            scores[method]+=abs(predicted-actual)
        windows+=1
    # Fixed tie break favours the simple recent baseline. No evaluation truth.
    return min(METHODS,key=lambda m:scores[m]) if windows else 'mean56', scores, windows


def forecast(request):
    cutoff=date.fromisoformat(request['as_of'])
    output={}
    for original in request['items']:
        item=at_cutoff(original,cutoff)
        method,_,_=select(item,cutoff)
        model=fitted(item,cutoff,method)
        output[item['sku']]=[model(cutoff+timedelta(days=d)) for d in range(1,request['horizon_days']+1)]
    return output
