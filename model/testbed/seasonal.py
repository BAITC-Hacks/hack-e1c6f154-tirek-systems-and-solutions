"""Past-only selection of means and a seasonal/trend regression.

The internal validation target is original observed sales, not a cleaned target.
No latent demand, scenario, seed, future inventory or category labels are read.
Numeric failures and lack of recent exposure/analogue support are explicit errors.
"""
from collections import defaultdict
from datetime import date, timedelta
import math
import statistics

from .adapters import validate_prediction, validate_request
from .metrics import finite_sum

METHODS = ('mean56', 'mean168', 'seasonal_trend')
MAX_SUPPORT_AGE_DAYS = 168


class InsufficientHistory(ValueError):
    """No supported demand estimate; censored/missing observations are not zeros."""


def checked(value, context, *, nonnegative=False):
    if not math.isfinite(value) or (nonnegative and value < 0):
        raise ValueError(f'Nonfinite or invalid seasonal {context}')
    return value


def divide(numerator, denominator, context):
    if denominator <= 0 or not math.isfinite(denominator):
        raise ValueError(f'Invalid seasonal denominator: {context}')
    result = checked(numerator / denominator, context)
    if numerator != 0 and result == 0:
        raise ValueError(f'Numeric underflow in seasonal {context}')
    return result


def multiply(left, right, context):
    result = checked(left * right, context)
    if left != 0 and right != 0 and result == 0:
        raise ValueError(f'Numeric underflow in seasonal {context}')
    return result


def promotion(item, stamp):
    factor = 1.0
    for promo in item['known_promotions']:
        if promo['start_date'] <= stamp <= promo['end_date']:
            factor = multiply(factor, promo['planned_multiplier'], 'promotion product')
    if factor <= 0:
        raise ValueError('Seasonal promotion product must be positive')
    return factor


def at_cutoff(item, cutoff):
    """Rebuild every inner training input at its own information date."""
    stamp = cutoff.isoformat()
    result = dict(item)
    for field in ('history', 'events', 'analogue_history'):
        result[field] = sorted((r for r in item[field] if r['date'] <= stamp), key=lambda r: r['date'])
    result['known_promotions'] = [p for p in item['known_promotions'] if p['announced_at'] <= stamp]
    return result


def observations(item):
    rows = sorted(item['history'], key=lambda r: r['date'])
    usable = [r for r in rows if r['complete'] and r['availability_fraction'] > 0]
    # Compare like-for-like baseline demand after known promotion/exposure removal.
    adjusted = [divide(divide(r['observed_quantity'], promotion(item, r['date']), 'promotion normalization'),
                       r['availability_fraction'], 'exposure normalization') for r in usable]
    typical = statistics.median(adjusted) if adjusted else 0
    excluded = defaultdict(float)
    # Sparse positive purchases alone never establish that a purchase is a project.
    if typical > 0:
        clients = defaultdict(list)
        for event in item['events']:
            if event['quantity'] > 0:
                clients[event['client_id']].append(event)
        for events in clients.values():
            ordered = sorted(events, key=lambda e: e['date'])
            first, last = (date.fromisoformat(ordered[index]['date']) for index in (0, -1))
            # Two purchases at least one week apart already demonstrate recurrence.
            # A project split over one short episode does not establish recurrence.
            if (last - first).days >= 7:
                continue
            start = first
            group = [e for e in ordered if (date.fromisoformat(e['date']) - start).days < 7]
            normalized_total = finite_sum(divide(e['quantity'], promotion(item, e['date']), 'client promotion normalization')
                                          for e in group)
            if divide(normalized_total, typical, 'client size ratio') > 8:
                for event in group:
                    excluded[event['date']] = finite_sum((excluded[event['date']], event['quantity']))
    return [(date.fromisoformat(r['date']),
             divide(max(0, r['observed_quantity'] - excluded[r['date']]),
                    promotion(item, r['date']), 'observed promotion normalization'),
             r['availability_fraction']) for r in usable]


def solve(matrix, target):
    """Small pivoted linear system with explicit finite arithmetic checks."""
    n = len(target)
    a = [[checked(value, 'linear system') for value in row] + [checked(value, 'linear target')]
         for row, value in zip(matrix, target)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda row: abs(a[row][col]))
        a[col], a[pivot] = a[pivot], a[col]
        if abs(a[col][col]) < 1e-12:
            raise ValueError('Singular seasonal fit')
        scale = a[col][col]
        a[col] = [checked(v / scale, 'linear normalization') for v in a[col]]
        for row in range(n):
            if row == col:
                continue
            factor = a[row][col]
            a[row] = [checked(v - factor * w, 'linear elimination') for v, w in zip(a[row], a[col])]
    return [row[-1] for row in a]


def features(day, cutoff):
    phase = 2 * math.pi * day.toordinal() / 365.25
    return [1, (day - cutoff).days / 365.25, math.sin(phase), math.cos(phase), math.sin(2*phase), math.cos(2*phase)]


def fitted(item, cutoff, method):
    if method not in METHODS:
        raise ValueError(f'Unknown seasonal method: {method}')
    # Public helpers are safe even when called directly rather than via select.
    item = at_cutoff(item, cutoff)
    obs = observations(item)
    days = 56 if method == 'mean56' else 168
    recent = [r for r in obs if 0 <= (cutoff-r[0]).days < days]
    if not recent:
        # A known stockout in the short window cannot imply zero demand. A longer
        # supported window is a documented fallback, not invented availability.
        recent = [r for r in obs if 0 <= (cutoff-r[0]).days < MAX_SUPPORT_AGE_DAYS]
    analogue_rows = [r for r in item['analogue_history']
                     if 0 <= (cutoff-date.fromisoformat(r['date'])).days < MAX_SUPPORT_AGE_DAYS]
    if not recent and not analogue_rows:
        raise InsufficientHistory('No complete observed exposure or supplied analogue within 168 days')
    exposure = finite_sum(r[2] for r in recent)
    rate = divide(finite_sum(r[1] for r in recent), exposure, 'recent rate') if recent else 0
    if analogue_rows and exposure < 28:
        analogue = divide(finite_sum(r['quantity'] for r in analogue_rows), len(analogue_rows), 'analogue mean')
        weight = exposure / 28
        rate = finite_sum((multiply(weight, rate, 'own-history blend'),
                           multiply(1-weight, analogue, 'analogue blend')))
    def flat(day):
        return checked(multiply(rate, promotion(item, day.isoformat()), 'flat forecast'), 'flat forecast', nonnegative=True)
    if method != 'seasonal_trend' or len(obs) < 280 or (obs[-1][0]-obs[0][0]).days < 365:
        return flat
    if sum(r[1] > 0 for r in obs) / len(obs) < .4:
        return flat
    bins = defaultdict(list)
    for row in obs:
        bins[(cutoff-row[0]).days//7].append(row)
    training = []
    for block, rows in sorted(bins.items()):
        exposure = finite_sum(r[2] for r in rows)
        if exposure < 3:
            continue
        midpoint = cutoff-timedelta(days=7*block+3)
        weekly_rate = divide(finite_sum(r[1] for r in rows), exposure, 'weekly rate')
        training.append((features(midpoint, cutoff), math.log(weekly_rate+.25), exposure))
    if len(training) < 45:
        return flat
    matrix = [[finite_sum(w*x[i]*x[j] for x, y, w in training)+(1e-3 if i == j and i else 0)
               for j in range(6)] for i in range(6)]
    target = [finite_sum(w*x[i]*y for x, y, w in training) for i in range(6)]
    coefficients = solve(matrix, target)
    def curve(day):
        log_rate = finite_sum(a*b for a, b in zip(coefficients, features(day, cutoff)))
        try:
            return checked(max(0, math.exp(log_rate)-.25), 'seasonal curve', nonnegative=True)
        except OverflowError as exc:
            raise ValueError('Numeric overflow in seasonal exponential') from exc
    expected = finite_sum(multiply(curve(day), exposure, 'calibration expected') for day, y, exposure in obs)
    observed = finite_sum(y for day, y, exposure in obs)
    if expected == 0 and observed > 0:
        raise ValueError('Seasonal fit has no expected support for positive observations')
    calibration = divide(observed, expected, 'calibration') if expected else 1
    by_weekday = defaultdict(lambda: [[], []])
    for day, y, exposure in obs:
        by_weekday[day.weekday()][0].append(y)
        by_weekday[day.weekday()][1].append(multiply(multiply(curve(day), calibration, 'weekday calibration'), exposure, 'weekday exposure'))
    factors = {k: divide(finite_sum(a)+10, finite_sum(b)+10, 'weekday factor') for k, (a, b) in by_weekday.items()}
    def seasonal(day):
        result = multiply(curve(day), calibration, 'seasonal calibration')
        result = multiply(result, factors.get(day.weekday(), 1), 'weekday forecast')
        return checked(multiply(result, promotion(item, day.isoformat()), 'promoted seasonal forecast'), 'forecast', nonnegative=True)
    return seasonal


def select(item, cutoff):
    item = at_cutoff(item, cutoff)
    scores = {method: 0. for method in METHODS}
    windows = 0
    for lag in (84, 56, 28):
        origin = cutoff-timedelta(days=lag)
        train = at_cutoff(item, origin)
        if len(train['history']) < 168:
            continue
        held = [r for r in item['history'] if origin.isoformat() < r['date'] <= (origin+timedelta(days=28)).isoformat()
                and r['complete'] and r['availability_fraction'] > 0]
        if finite_sum(r['availability_fraction'] for r in held) < 14:
            continue
        # Keep the raw observed validation target. A project heuristic is not
        # ground truth and must never clean these outcomes to flatter a score.
        actual = finite_sum(r['observed_quantity'] for r in held)
        try:
            models = {method: fitted(train, origin, method) for method in METHODS}
        except InsufficientHistory:
            continue
        for method, model in models.items():
            predicted = finite_sum(multiply(model(date.fromisoformat(r['date'])), r['availability_fraction'], 'held exposure') for r in held)
            scores[method] = finite_sum((scores[method], abs(predicted-actual)))
        windows += 1
    return min(METHODS, key=lambda m: scores[m]) if windows else 'mean56', scores, windows


def forecast(request):
    # Direct calls preserve order invariance; the external contract separately
    # rejects unsorted imports. Do not silently drop future rows or announcements.
    normalized = dict(request)
    normalized['items'] = [dict(item, history=sorted(item['history'], key=lambda r: r['date'])) for item in request['items']]
    validate_request(normalized)
    cutoff = date.fromisoformat(normalized['as_of'])
    output = {}
    for original in normalized['items']:
        item = at_cutoff(original, cutoff)
        method, _, _ = select(item, cutoff)
        model = fitted(item, cutoff, method)
        output[item['sku']] = [model(cutoff+timedelta(days=d)) for d in range(1, normalized['horizon_days']+1)]
    return validate_prediction(normalized, output)
