"""Offline procurement fixtures, strict checker and illustrative calculator.

Quantity and unit_cost use base ``unit``; MOQ/multiple use ``order_unit``.
Canonical YYYY-MM-DD dates: receipts at day start, consumption at day end.
Day 1 follows as_of; zero-day lead is available before day 1. Inputs are parsed as
Decimal, then exact Fraction arithmetic preserves money and planning ratios until
JSON output. This avoids both binary-float errors and repeating-decimal drift.
Physical quantum, positive multiple and conversion factor are supported within
[1e-12, 1e12]; MOQ is supported within [0, 1e12]. Outside this explicit reference
range the result is DATA-01/needs_data, not a precision-dependent exception.
Growth rate must exceed -1; a category minimum is null or strictly between 0 and
1, matching OpenAPI CategoryPolicy/GrowthAdjustment. The local economics contract
deliberately allows either cost to be zero (positive sum), including q=0/q=1;
zero prices/leads are also local boundaries, not claimed HTTP compatibility.
"""
from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal
from fractions import Fraction
import json
import math
from pathlib import Path

D = lambda value: Decimal(str(value))
F = lambda value: value if isinstance(value, Fraction) else Fraction(D(value))
ZERO = Fraction(0)
MASS_TOLERANCE = F('1e-9')
MIN_CONSTRAINT = F('1e-12')
MAX_CONSTRAINT = F('1e12')


def numeric(value, minimum=0, positive=False):
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and D(value).is_finite() and (minimum is None or D(value) >= minimum)
            and (not positive or D(value) > 0))


def iso_date(value):
    if not isinstance(value, str):
        raise ValueError('Date must be canonical YYYY-MM-DD')
    result = date.fromisoformat(value)
    if result.isoformat() != value:
        raise ValueError('Date must be canonical YYYY-MM-DD')
    return result


def valid_date(value):
    try:
        iso_date(value)
        return True
    except (ValueError, TypeError):
        return False


def number(value):
    value = F(value)
    if value.denominator == 1:
        return value.numerator
    result = float(value)
    if not math.isfinite(result):
        raise ValueError('Calculated quantity/cost exceeds finite JSON range')
    return result


def round_up(value, step):
    return number(math.ceil(F(value) / F(step)) * F(step))


def _identity(value):
    return value if isinstance(value, str) and value.strip() else None


def _supplier_groups(items):
    """A missing supplier remains needs_data and never becomes a JSON object key."""
    suppliers = sorted({_identity(p.get('supplier_id')) for p in items}
                       - {None})
    return {supplier: [p['sku'] for p in items if _identity(p.get('supplier_id')) == supplier]
            for supplier in suppliers}


def _item_errors(request, p):
    errors = []
    def require(condition, field):
        if not condition and field not in errors:
            errors.append(field)
    h = request.get('horizon_days')
    require(type(h) is int and h > 0, 'horizon_days')
    require(valid_date(request.get('as_of')), 'as_of')
    if type(h) is int and h > 0 and valid_date(request.get('as_of')):
        try:
            iso_date(request['as_of']) + timedelta(days=h)
        except (OverflowError, ValueError):
            require(False, 'horizon_days')
    require(request.get('budget_kzt') is None or numeric(request['budget_kzt']), 'budget_kzt')
    for field in ('sku', 'supplier_id', 'category_raw', 'unit', 'order_unit'):
        require(_identity(p.get(field)) is not None, field)
    if p.get('free_stock') is None and p.get('on_hand') is None:
        errors.append('stock')
    require(p.get('stock_current') is True, 'current_stock')
    if p.get('free_stock') is None and p.get('reserved') is None:
        errors.append('reserved')
    for field in ('on_hand', 'free_stock', 'reserved', 'unit_cost'):
        require(p.get(field) is None or numeric(p[field]), field)
    for field in ('lead_time_days', 'review_period_days'):
        require(type(p.get(field)) is int and p[field] >= 0, field)
    if type(p.get('lead_time_days')) is int and type(p.get('review_period_days')) is int:
        require(p['lead_time_days'] + p['review_period_days'] == h, 'horizon_equals_lead_plus_review')
    daily = p.get('daily_mean')
    require(isinstance(daily, list) and len(daily) == h and all(numeric(v) for v in daily), 'daily_mean')
    require(numeric(p.get('unit_quantum'), positive=True), 'unit_quantum')
    factor = 1 if p.get('unit') == p.get('order_unit') else p.get('order_to_base_factor')
    require(numeric(factor, positive=True), 'unit_conversion')
    moq, multiple = p.get('min_order_qty'), p.get('order_multiple')
    require(moq is None or numeric(moq), 'valid_order_constraints')
    require(multiple is None or numeric(multiple, positive=True), 'valid_order_constraints')
    for constrained in (p.get('unit_quantum'), factor, multiple):
        if numeric(constrained, positive=True):
            require(MIN_CONSTRAINT <= F(constrained) <= MAX_CONSTRAINT,
                    'supported_order_constraints_range')
    if numeric(moq):
        require(F(moq) <= MAX_CONSTRAINT, 'supported_order_constraints_range')
    if numeric(multiple, positive=True) and numeric(factor, positive=True) and numeric(p.get('unit_quantum'), positive=True):
        require((F(multiple) * F(factor) / F(p['unit_quantum'])).denominator == 1, 'valid_order_constraints')
    policies = request.get('category_policies')
    require(isinstance(policies, dict), 'category_policies')
    policy = policies.get(p.get('category_raw')) if isinstance(policies, dict) and isinstance(p.get('category_raw'), str) else None
    economics = p.get('economics')
    require(policy is not None or economics is not None, 'policy_or_economics')
    if policy is not None:
        valid = isinstance(policy, dict) and numeric(policy.get('target_quantile')) and 0 < policy['target_quantile'] < 1
        floor = policy.get('minimum_target_quantile') if isinstance(policy, dict) else None
        require(valid and (floor is None or numeric(floor, positive=True) and floor < 1
                           and floor <= policy['target_quantile']), 'valid_category_policy')
    if economics is not None:
        valid = isinstance(economics, dict) and numeric(economics.get('underage_cost')) and numeric(economics.get('overage_cost'))
        require(valid and D(economics['underage_cost']) + D(economics['overage_cost']) > 0, 'valid_economics')
        require(isinstance(economics, dict) and type(economics.get('horizon_days')) is int and economics['horizon_days'] == h, 'economics_horizon')
        require(isinstance(economics, dict) and isinstance(economics.get('source'), str) and bool(economics['source'].strip()), 'economics_source')
    dist = p.get('horizon_distribution')
    valid = isinstance(dist, list) and bool(dist) and all(isinstance(v, dict) and numeric(v.get('quantity')) and numeric(v.get('probability')) for v in dist)
    if valid:
        mass = sum((F(v['probability']) for v in dist), ZERO)
        valid = mass > 0 and abs(mass - 1) <= MASS_TOLERANCE
    require(valid, 'horizon_distribution')
    for key, stamp, unit_error in (('inbound', 'expected_at', 'inbound_unit_conversion'), ('material_requirements', 'needed_at', 'material_unit_conversion')):
        entries = p.get(key, [])
        require(isinstance(entries, list), key)
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict):
                require(False, key)
                continue
            require(numeric(entry.get('quantity')), key + '.quantity')
            require(valid_date(entry.get(stamp)), key + '.' + stamp)
            require(entry.get('unit', p.get('unit')) == p.get('unit'), unit_error)
            if key == 'inbound':
                require(entry.get('status') in ('confirmed', 'cancelled', 'unconfirmed', 'planned', 'draft'), 'inbound.status')
            else:
                accounted = entry.get('already_accounted_quantity')
                require(numeric(accounted) and numeric(entry.get('quantity')) and accounted <= entry['quantity'], 'material_requirements.already_accounted_quantity')
    growths = p.get('growth_adjustments', [])
    require(isinstance(growths, list), 'growth_adjustments')
    included = p.get('growth_already_included', [])
    require(isinstance(included, list) and all(isinstance(v, str) and v for v in included), 'growth_already_included')
    seen = {}
    for growth in growths if isinstance(growths, list) else []:
        if not isinstance(growth, dict):
            require(False, 'growth_adjustments')
            continue
        identity = growth.get('id')
        require(isinstance(identity, str) and bool(identity), 'growth_adjustments.id')
        require(numeric(growth.get('rate'), minimum=-1) and growth['rate'] > -1, 'growth_adjustments.rate')
        require(isinstance(growth.get('source'), str) and bool(growth['source'].strip()), 'growth_adjustments.source')
        require(valid_date(growth.get('valid_from')) and valid_date(growth.get('valid_to')) and growth['valid_from'] <= growth['valid_to'], 'growth_adjustments.period')
        if isinstance(identity, str):
            require(identity not in seen or seen[identity] == growth, 'growth_adjustments.conflicting_id')
            seen[identity] = growth
    return errors


def _trajectory(free, demand, receipts, commitments, eta, quantity=ZERO):
    """Separate backlog and lost-sales views; incoming supply precedes consumption."""
    balance = physical = free
    path = []
    for index, value in enumerate(demand, 1):
        incoming = receipts[index - 1] + (quantity if index == eta else ZERO)
        required = value + commitments[index - 1]
        balance += incoming - required
        unmet = max(ZERO, required - physical - incoming)
        physical = max(ZERO, physical + incoming - required)
        path.append({'day': index, 'balance': balance, 'unmet': unmet})
    return path


def reference_calculate(request):
    """Reference only. Allocate the economic horizon target by daily-mean shape.

    That planning profile is not a calibrated sequence of daily quantiles. Report
    the raw-mean risk separately rather than silently overriding the chosen policy.
    """
    if not isinstance(request, dict) or not isinstance(request.get('items'), list) or not request['items']:
        raise ValueError('request.items must be a nonempty list')
    if any(not isinstance(p, dict) for p in request['items']):
        raise ValueError('Every request item must be an object')
    identities = [p.get('sku') for p in request['items']]
    if any(not isinstance(s, str) or not s for s in identities) or len(set(identities)) != len(identities):
        raise ValueError('Request SKUs must be nonempty and unique')
    items, exact_costs = [], []
    for p in request['items']:
        missing = _item_errors(request, p)
        row = {'sku': p['sku'], 'supplier_id': _identity(p.get('supplier_id')), 'unit': _identity(p.get('unit')), 'quantity': None,
               'status': 'needs_data' if missing else 'ok', 'missing': missing, 'rules': [], 'reason': '',
               'evidence': {}, 'unit_cost': p.get('unit_cost') if numeric(p.get('unit_cost')) else None}
        items.append(row)
        if missing:
            row.update(rules=['DATA-01'], reason='Calculation blocked: ' + ', '.join(missing), evidence={'DATA-01': {'invalid_or_missing_fields': missing}})
            if 'supported_order_constraints_range' in missing:
                row['evidence']['DATA-01']['supported_order_constraints_range'] = {
                    'positive_quantum_multiple_factor_min': 1e-12,
                    'quantum_multiple_factor_moq_max': 1e12, 'moq_min': 0}
            continue
        horizon, cutoff = request['horizon_days'], iso_date(request['as_of'])
        dates = [cutoff + timedelta(days=d) for d in range(1, horizon + 1)]
        policy, economics = request['category_policies'].get(p['category_raw']), p.get('economics')
        q = F(economics['underage_cost']) / (F(economics['underage_cost']) + F(economics['overage_cost'])) if economics is not None else F(policy['target_quantile'])
        q = max(q, F(policy.get('minimum_target_quantile') or 0) if policy else ZERO)
        rule = 'POLICY-01' if economics is not None else 'POLICY-02'
        row['rules'].append(rule)
        dist = sorted((F(v['quantity']), F(v['probability'])) for v in p['horizon_distribution'] if v['probability'] > 0)
        mass, target, running = sum((v[1] for v in dist), ZERO), ZERO, ZERO
        # q*mass consistently normalizes accepted probability roundoff, even q=1.
        if q > 0:
            target = dist[-1][0]
            for value, probability in dist:
                running += probability
                if running >= q * mass:
                    target = value
                    break
        daily = [F(v) for v in p['daily_mean']]
        original_mean = sum(daily, ZERO)
        applied, growth_evidence = set(p.get('growth_already_included', [])), []
        for growth in p.get('growth_adjustments', []):
            if growth['id'] in applied:
                continue
            touched = 0
            start, end = iso_date(growth['valid_from']), iso_date(growth['valid_to'])
            for index, stamp in enumerate(dates):
                if start <= stamp <= end:
                    daily[index] *= 1 + F(growth['rate'])
                    touched += 1
            applied.add(growth['id'])
            if touched:
                growth_evidence.append({'id': growth['id'], 'source': growth['source'], 'rate': growth['rate'], 'days': touched})
        mean = sum(daily, ZERO)
        if original_mean:
            target *= mean / original_mean
        if not mean and target:
            row.update(status='needs_data', rules=['DATA-01'], missing=['planning_profile'], reason='Positive horizon target cannot be allocated over zero daily mean.', evidence={'DATA-01': {'invalid_or_missing_fields': ['planning_profile']}})
            continue
        if growth_evidence:
            row['rules'].append('DEMAND-04')
            row['evidence']['DEMAND-04'] = {'applied_adjustments': growth_evidence}
        cumulative, allocated, planning = ZERO, ZERO, []
        for demand in daily:
            cumulative += demand
            next_total = target * cumulative / mean if mean else ZERO
            planning.append(next_total - allocated)
            allocated = next_total
        free = F(p['free_stock']) if p.get('free_stock') is not None else F(p['on_hand']) - F(p['reserved'])
        receipts, commitments = [ZERO] * horizon, [ZERO] * horizon
        for inbound in p.get('inbound', []):
            stamp = iso_date(inbound['expected_at'])
            if inbound['status'] == 'confirmed' and cutoff < stamp <= dates[-1]:
                receipts[(stamp - cutoff).days - 1] += F(inbound['quantity'])
        for requirement in p.get('material_requirements', []):
            stamp = iso_date(requirement['needed_at'])
            if stamp <= dates[-1]:
                commitments[max(0, (stamp - cutoff).days - 1)] += F(requirement['quantity']) - F(requirement['already_accounted_quantity'])
        incoming, uncovered = sum(receipts, ZERO), sum(commitments, ZERO)
        aggregate_need = max(ZERO, target + uncovered - free - incoming)
        eta = max(1, p['lead_time_days'])
        planned_without = _trajectory(free, planning, receipts, commitments, eta)
        mean_without = _trajectory(free, daily, receipts, commitments, eta)
        temporal = max([ZERO] + [-r['balance'] for r in planned_without if r['day'] >= eta])
        need = max(aggregate_need, temporal)
        factor = F(1) if p['order_unit'] == p['unit'] else F(p['order_to_base_factor'])
        moq = F(p['min_order_qty']) * factor if p.get('min_order_qty') is not None else ZERO
        multiple = F(p['order_multiple']) * factor if p.get('order_multiple') is not None else F(p['unit_quantum'])
        quantity = (math.ceil(max(need, moq) / multiple) * multiple) if need > 0 else ZERO
        unknown = [k for k in ('min_order_qty', 'order_multiple') if p.get(k) is None]
        if unknown:
            row['status'] = 'needs_review'
            row['rules'].append('SUPPLY-04')
            row['evidence']['SUPPLY-04'] = {'unknown_constraints': unknown, 'known_constraints_applied': True}
        row['rules'].append('SUPPLY-03')
        row['evidence']['SUPPLY-03'] = {'min_order_base': number(moq) if p.get('min_order_qty') is not None else None,
            'multiple_base': number(multiple) if p.get('order_multiple') is not None else None,
            'physical_quantum': p['unit_quantum'], 'order_unit': p['order_unit'], 'order_to_base_factor': number(factor), 'quantity_before_rounding': number(need)}
        planned_after = _trajectory(free, planning, receipts, commitments, eta, quantity)
        mean_after = _trajectory(free, daily, receipts, commitments, eta, quantity)
        early = max([ZERO] + [-r['balance'] for r in mean_without if r['day'] < eta])
        mean_unmet = sum((r['unmet'] for r in mean_after), ZERO)
        planning_unmet = sum((r['unmet'] for r in planned_after), ZERO)
        if early or temporal > aggregate_need or mean_unmet:
            row['rules'].append('SUPPLY-02')
            row['evidence']['SUPPLY-02'] = {'ordinary_receipt_day': eta, 'early_mean_shortfall': number(early),
                'aggregate_need': number(aggregate_need), 'temporal_lower_bound': number(temporal),
                'mean_unmet_with_order': number(mean_unmet), 'planning_unmet_with_order': number(planning_unmet)}
        if p.get('reserved') or any(commitments):
            row['rules'].append('SUPPLY-01')
            row['evidence']['SUPPLY-01'] = {'free_stock_source': 'provided_net' if p.get('free_stock') is not None else 'on_hand_minus_reserved', 'free_stock': number(free), 'uncovered_commitments': number(uncovered)}
        row['evidence'][rule] = {'category_raw': p['category_raw'], 'target_quantile': number(q), 'target_stock': number(target),
            'distribution_mass_input': number(mass), 'distribution_mass_normalized': mass != 1, 'economics_source': economics['source'] if economics is not None else None}
        row.update(quantity=number(quantity), forecast_mean=number(mean), target_stock=number(target), target_quantile=number(q),
            free_stock=number(free), material_uncovered=number(uncovered), inbound_in_horizon=number(incoming),
            first_deficit_day=next((r['day'] for r in mean_without if r['balance'] < 0), None), early_shortfall=number(early), requires_expedite=early > 0,
            economics_source=economics['source'] if economics is not None else None,
            planning_profile='horizon_target_proportional_to_daily_mean', aggregate_need=number(aggregate_need), temporal_lower_bound=number(temporal),
            planning_unmet_with_order=number(planning_unmet), mean_unmet_with_order=number(mean_unmet), mean_risk_after_order=mean_unmet > 0,
            mean_first_unmet_day_with_order=next((r['day'] for r in mean_after if r['unmet'] > 0), None),
            inventory_projection=[{'day': a['day'], 'date': dates[a['day'] - 1].isoformat(),
                'mean_balance_without_order': number(c['balance']), 'mean_balance_with_order': number(a['balance']),
                'mean_unmet_with_order': number(a['unmet']), 'planning_balance_with_order': number(b['balance']),
                'planning_unmet_with_order': number(b['unmet'])} for a, b, c in zip(mean_after, planned_after, mean_without)])
        row['reason'] = (f"{rule}: horizon quantile {number(q)} selects {number(target)} {p['unit']}; free stock {number(free)}, "
            f"uncovered obligations {number(uncovered)}, confirmed inbound {number(incoming)}. Aggregate need {number(aggregate_need)}, "
            f"timing lower bound {number(temporal)}; order {number(quantity)} {p['unit']} after known supplier constraints. "
            f"Ordinary receipt day {eta}; early mean shortfall {number(early)}, remaining mean unmet demand {number(mean_unmet)}. Price is per base unit.")
        if p.get('unit_cost') is not None:
            exact_costs.append(quantity * F(p['unit_cost']))
    known = sum(exact_costs, ZERO)
    unknown_price = any(r['quantity'] is not None and r['quantity'] > 0 and r.get('unit_cost') is None for r in items)
    incomplete = any(r['quantity'] is None for r in items)
    budget = request.get('budget_kzt')
    valid_budget = budget is not None and numeric(budget)
    rules = []
    if valid_budget and unknown_price:
        rules.append('BUDGET-01')
    if valid_budget and known > F(budget):
        rules.append('BUDGET-02')
    return {'items': items, 'known_cost_kzt': number(known), 'total_cost_kzt': None if unknown_price or incomplete else number(known),
        'budget_excess_kzt': number(max(ZERO, known - F(budget))) if valid_budget else None,
        'approval_blocked': bool(rules) or any(r['status'] != 'ok' for r in items), 'rules': rules,
        'supplier_groups': _supplier_groups(items)}


def compare(expected, actual, path='result'):
    """Subset value assertions, following mandatory whole-response validation."""
    failures = []
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return [f'{path}: expected object, got {actual!r}']
        for key, value in expected.items():
            failures += ([f'{path}.{key}: missing'] if key not in actual else compare(value, actual[key], f'{path}.{key}'))
    elif isinstance(expected, list):
        if not isinstance(actual, list) or len(expected) != len(actual):
            return [f'{path}: list length/type differs']
        for index, value in enumerate(expected):
            failures += compare(value, actual[index], f'{path}[{index}]')
    elif isinstance(expected, (int, float)) and not isinstance(expected, bool):
        if not numeric(actual, minimum=None) or abs(D(expected) - D(actual)) > max(D('1e-8'), abs(D(expected)) * D('1e-9')):
            failures.append(f'{path}: expected {expected!r}, got {actual!r}')
    elif type(expected) is not type(actual) or expected != actual:
        failures.append(f'{path}: expected {expected!r}, got {actual!r}')
    return failures


def validate_response(request, actual):
    """Common typed schema and cross-field invariants, independent of expected."""
    errors = []
    def require(condition, message):
        if not condition:
            errors.append(message)
    def strings(value, nonempty=False):
        return isinstance(value, list) and (not nonempty or bool(value)) and all(isinstance(v, str) and v.strip() for v in value)
    if not isinstance(actual, dict):
        return ['result must be an object']
    required_top = {'items', 'known_cost_kzt', 'total_cost_kzt', 'budget_excess_kzt',
                    'approval_blocked', 'rules', 'supplier_groups'}
    require(required_top <= actual.keys(), 'required top-level fields missing')
    def json_values(value):
        if value is None or isinstance(value, (str, bool)):
            return True
        if isinstance(value, (int, float)):
            return numeric(value, minimum=None)
        if isinstance(value, list):
            return all(json_values(v) for v in value)
        if isinstance(value, dict):
            return all(isinstance(k, str) and json_values(v) for k, v in value.items())
        return False
    require(json_values(actual), 'response contains nonfinite or non-JSON values')
    rows = actual.get('items')
    if not isinstance(rows, list) or len(rows) != len(request['items']):
        return ['items must contain exactly every requested SKU']
    if any(not isinstance(r, dict) for r in rows):
        return ['every result item must be an object']
    require([r.get('sku') for r in rows] == [p['sku'] for p in request['items']], 'item SKU/order mismatch')
    incomplete, unknown_price, known = False, False, ZERO
    for index, (p, row) in enumerate(zip(request['items'], rows)):
        prefix, status, quantity = f'items[{index}]', row.get('status'), row.get('quantity')
        require({'sku', 'supplier_id', 'unit', 'quantity', 'status', 'missing', 'rules',
                 'reason', 'evidence', 'unit_cost'} <= row.keys(), prefix + ': required fields missing')
        require(row.get('supplier_id') == _identity(p.get('supplier_id')), prefix + ': supplier identity mismatch')
        require(row.get('unit') == _identity(p.get('unit')), prefix + ': base unit mismatch')
        require(status in ('ok', 'needs_review', 'needs_data'), prefix + ': invalid status')
        require(isinstance(row.get('reason'), str) and bool(row['reason'].strip()), prefix + ': reason must be text')
        require(strings(row.get('rules'), True), prefix + ': rules must be a nonempty string list')
        require(strings(row.get('missing')), prefix + ': missing must be a string list')
        evidence = row.get('evidence')
        require(isinstance(evidence, dict) and bool(evidence), prefix + ': evidence must be an object')
        rule_ids = row['rules'] if strings(row.get('rules'), True) else []
        if isinstance(evidence, dict):
            require(all(rule in evidence and isinstance(evidence[rule], dict) and evidence[rule] for rule in rule_ids), prefix + ': rule evidence missing')
        if status == 'needs_data':
            require(quantity is None, prefix + ': needs_data requires null quantity')
            require(strings(row.get('missing'), True), prefix + ': needs_data requires missing fields')
            require('DATA-01' in rule_ids, prefix + ': needs_data requires DATA-01')
        else:
            require(numeric(quantity), prefix + ': quantity must be finite nonnegative')
            require(row.get('missing') == [], prefix + ': ready/review quantity has missing data')
        if quantity is None:
            incomplete = True
        if status != 'needs_data':
            require(_identity(row.get('supplier_id')) is not None
                    and _identity(row.get('unit')) is not None,
                    prefix + ': calculated row requires supplier and unit identities')
            require({'forecast_mean', 'target_stock', 'target_quantile', 'free_stock',
                     'material_uncovered', 'inbound_in_horizon', 'first_deficit_day',
                     'early_shortfall', 'requires_expedite', 'economics_source'} <= row.keys(),
                    prefix + ': required calculated fields missing')
            require(('POLICY-01' if p.get('economics') is not None else 'POLICY-02') in rule_ids
                    and 'DATA-01' not in rule_ids, prefix + ': policy/status rule conflict')
            for field in ('forecast_mean', 'target_stock', 'target_quantile', 'material_uncovered', 'inbound_in_horizon', 'early_shortfall'):
                require(numeric(row.get(field)), prefix + ': invalid ' + field)
            require(numeric(row.get('free_stock'), minimum=None), prefix + ': invalid free_stock')
            require(numeric(row.get('target_quantile')) and row['target_quantile'] <= 1, prefix + ': quantile outside [0,1]')
            require(type(row.get('requires_expedite')) is bool, prefix + ': requires_expedite must be bool')
            if numeric(row.get('early_shortfall')):
                require(row.get('requires_expedite') == (row['early_shortfall'] > 0), prefix + ': expedite/shortfall conflict')
            first = row.get('first_deficit_day')
            require(first is None or type(first) is int and 1 <= first <= request['horizon_days'], prefix + ': invalid deficit day')
            economics = p.get('economics')
            require(row.get('economics_source') == (economics.get('source') if isinstance(economics, dict) else None), prefix + ': economics provenance mismatch')
            require(row.get('unit_cost') == p.get('unit_cost') and (row.get('unit_cost') is None or numeric(row['unit_cost'])), prefix + ': invalid base-unit price')
            if numeric(quantity) and numeric(p.get('unit_quantum'), positive=True):
                require((F(quantity) / F(p['unit_quantum'])).denominator == 1, prefix + ': physical quantum violated')
                factor = 1 if p['unit'] == p['order_unit'] else p.get('order_to_base_factor')
                if quantity > 0 and numeric(factor, positive=True):
                    if numeric(p.get('min_order_qty')):
                        require(F(quantity) >= F(p['min_order_qty']) * F(factor), prefix + ': MOQ violated')
                    if numeric(p.get('order_multiple'), positive=True):
                        require((F(quantity) / (F(p['order_multiple']) * F(factor))).denominator == 1, prefix + ': multiple violated')
            if p.get('min_order_qty') is None or p.get('order_multiple') is None:
                require(status == 'needs_review' and 'SUPPLY-04' in rule_ids, prefix + ': unknown supply constraint requires review')
        if numeric(quantity):
            cost = row.get('unit_cost')
            if cost is None and quantity > 0:
                unknown_price = True
            elif numeric(cost):
                known += F(quantity) * F(cost)
    require(type(actual.get('approval_blocked')) is bool, 'approval_blocked must be bool')
    require(strings(actual.get('rules')), 'top-level rules must be a string list')
    require(numeric(actual.get('known_cost_kzt')), 'known cost must be finite nonnegative')
    errors += compare(number(known), actual.get('known_cost_kzt'), 'known_cost_kzt')
    errors += compare(None if incomplete or unknown_price else number(known), actual.get('total_cost_kzt'), 'total_cost_kzt')
    expected_groups = _supplier_groups(request['items'])
    require(actual.get('supplier_groups') == expected_groups, 'supplier_groups mismatch')
    budget, expected_rules = request.get('budget_kzt'), []
    if budget is not None and numeric(budget):
        if unknown_price:
            expected_rules.append('BUDGET-01')
        if known > F(budget):
            expected_rules.append('BUDGET-02')
        errors += compare(number(max(ZERO, known - F(budget))), actual.get('budget_excess_kzt'), 'budget_excess_kzt')
    else:
        require(actual.get('budget_excess_kzt') is None, 'budget_excess must be null without valid budget')
    require(actual.get('rules') == expected_rules, 'budget rule IDs do not match costs')
    require(actual.get('approval_blocked') == (bool(expected_rules) or any(r.get('status') != 'ok' for r in rows)), 'approval/status/budget conflict')
    return errors


def check_cases(calculator):
    fixture = json.loads(Path(__file__).with_name('fixtures').joinpath('business_cases.json').read_text())
    rows = []
    for case in fixture['cases']:
        try:
            request = deepcopy(case['input'])
            actual = calculator(deepcopy(request))
            failures = validate_response(request, actual) + compare(case['expected'], actual)
            for assertion in case.get('contains', []):
                value = actual
                for part in assertion['path']:
                    value = value[part]
                if not isinstance(value, list) or assertion['value'] not in value:
                    failures.append(f"Missing list member {assertion['value']} at {assertion['path']}")
        except Exception as exc:
            actual, failures = None, [f'{type(exc).__name__}: {exc}']
        rows.append({'id': case['id'], 'requirements': case['requirements'], 'passed': not failures, 'failures': failures, 'actual': actual})
    return rows
