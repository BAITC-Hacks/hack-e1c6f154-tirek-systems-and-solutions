"""Semantic integration fixtures; provider records below are MOCKS, not LLM calls."""
from copy import deepcopy
from unittest.mock import patch

import pytest

from backend.app.contracts import DomainError
from backend.app.demo import demo_dataset, make_demo, summary
from backend.app.pipeline import calculate
from model.decision.rules import build_ai_context


def request():
    return {'dataset_id': 'demo-systeme-v1', 'as_of_date': '2026-09-22', 'warehouse_ids': ['almaty'],
            'category_codes': [], 'horizon_days': 28, 'lead_time_days': 7, 'review_period_days': 21,
            'mode': 'scenario', 'category_policies': [], 'economic_profiles': [], 'growth_adjustments': [],
            'budget_kzt': None, 'request_ai_review': True}


def fixture():
    result = make_demo('semantic-fixture', request())
    item = result['response']['items'][0]
    detail = result['details'][item['item_id']]
    meta = result['response']['meta']
    return result, item, detail, meta


def run(result):
    with patch('backend.app.pipeline.make_demo', return_value=result):
        return calculate(demo_dataset(), request(), 'semantic-fixture')


def mock_review(result, item, detail, meta):
    item['ai'] = {'status': 'reviewed', 'verdict': 'needs_review', 'reasons': ['Свободный остаток 24 шт.'],
                  'evidence_ids': [item['evidence'][0]['id']], 'rule_ids': ['scenario-fixture'],
                  'suggested_action': 'Проверить данные', 'provider_model': 'MOCK-NOT-A-REAL-CALL'}
    result['ai_provenance'] = {item['item_id']: {
        'call_id': 'MOCK-CALL-NO-NETWORK', 'provider_called': True, 'provider_model': 'MOCK-NOT-A-REAL-CALL',
        'request_context': build_ai_context(
            evidence=item['evidence'], versions={'dataset_version': meta['dataset_version'],
                'policy_version': meta['policy_version'], 'calculation_revision': meta['revision']},
            allowed_rule_ids=list(dict.fromkeys([*detail['applied_rule_ids'], 'AI-01'])),
            recommended_quantity=item['recommended_quantity'],
            product_text=item['name'])}}


@pytest.mark.parametrize('field,value', [
    ('dataset_version', 'wrong-version'), ('data_as_of', '2026-09-21'), ('as_of_date', '2026-09-21'),
    ('mode', 'operational'), ('revision', 99), ('horizon_days', 14),
    ('calculation_id', 'foreign'), ('dataset_id', 'foreign')])
def test_pipeline_rejects_wrong_point_in_time_metadata(field, value):
    result, _, _, meta = fixture()
    meta[field] = value
    with pytest.raises(DomainError, match='Метаданные'):
        run(result)


@pytest.mark.parametrize('updates', [
    {'period_start': '2026-10-20', 'period_end': '2026-09-23'}, {'horizon_days': 14},
    {'period_end': '2026-10-21'}, {'p10': 300, 'p50': 200, 'p90': 100},
    {'p10': 300, 'p50': None, 'p90': 100}, {'mean': float('nan')}, {'p90': float('inf')}])
def test_pipeline_rejects_invalid_forecast_semantics(updates):
    result, item, _, _ = fixture()
    item['forecast'].update(updates)
    with pytest.raises(DomainError):
        run(result)


def test_pipeline_rejects_public_detail_disagreement_before_ai_normalization():
    result, item, detail, _ = fixture()
    detail['item'] = deepcopy(item)
    detail['item']['ai']['verdict'] = 'supports'
    with pytest.raises(DomainError, match='Публичная строка'):
        run(result)


def test_valid_mock_provenance_is_accepted_without_mutating_or_reordering_quantities():
    result, item, detail, meta = fixture()
    mock_review(result, item, detail, meta)
    before = deepcopy(result)
    checked = run(result)['response']['items'][0]
    assert checked['ai']['status'] == 'reviewed'
    assert checked['recommended_quantity'] == 144
    assert checked['final_quantity'] == 144
    assert result == before


@pytest.mark.parametrize('change', [
    {'verdict': None}, {'provider_model': None}, {'evidence_ids': ['invented']},
    {'rule_ids': ['APPROVE-AUTOMATICALLY']}, {'reasons': ['Остаток 9999 шт.']},
    {'suggested_action': 'Отправить заказ'}, {'approved': True}, {'reasons': [float('nan')]},
    {'status': 'not_requested', 'verdict': None}, {'status': 'unavailable', 'verdict': None}])
def test_invalid_mock_ai_fails_closed_preserving_deterministic_quantity(change):
    result, item, detail, meta = fixture()
    mock_review(result, item, detail, meta)
    item['ai'].update(change)
    checked = run(result)['response']['items'][0]
    assert checked['ai']['status'] == 'unavailable'
    assert checked['ai']['verdict'] is None
    assert checked['ai']['provider_model'] is None
    assert checked['recommended_quantity'] == 144
    assert checked['final_quantity'] == 144


@pytest.mark.parametrize('mutation', ['missing', 'not_called', 'wrong_model', 'stale_version', 'changed_value', 'changed_quantity'])
def test_missing_or_stale_provider_context_cannot_create_review(mutation):
    result, item, detail, meta = fixture()
    mock_review(result, item, detail, meta)
    record = result['ai_provenance'][item['item_id']]
    if mutation == 'missing':
        del result['ai_provenance']
    elif mutation == 'not_called':
        record['provider_called'] = False
    elif mutation == 'wrong_model':
        record['provider_model'] = 'ANOTHER-MOCK'
    elif mutation == 'stale_version':
        record['request_context']['versions']['calculation_revision'] = 2
    elif mutation == 'changed_value':
        record['request_context']['evidence'][0]['value'] = 25
    else:
        record['request_context']['recommended_quantity'] = 9999
    checked = run(result)['response']['items'][0]
    assert checked['ai']['status'] == 'unavailable'
    assert checked['recommended_quantity'] == 144


def test_unknown_rules_in_nonreviewed_ai_fail_closed():
    result, item, _, _ = fixture()
    item['ai']['rule_ids'] = ['invented']
    assert run(result)['response']['items'][0]['ai']['status'] == 'unavailable'
    assert run(result)['response']['items'][0]['ai']['rule_ids'] == ['AI-01']


@pytest.mark.parametrize('status,quantity', [('needs_data', 10), ('no_order', 10)])
def test_pipeline_rejects_status_quantity_contradiction(status, quantity):
    result, item, _, _ = fixture()
    item.update(decision_status=status, recommended_quantity=quantity)
    with pytest.raises(DomainError):
        run(result)


def test_pipeline_accepts_explicitly_labeled_provisional_quantity_but_not_final():
    result, item, detail, _ = fixture()
    item.update(decision_status='needs_data', recommended_quantity=144, final_quantity=None)
    item['issues'].extend([
        {'code': 'REQUIRED_INBOUND_QUANTITY', 'severity': 'error',
         'message': 'Нужно уточнить inbound.', 'affected_skus': [item['sku']], 'source_reference': None},
        {'code': 'PROVISIONAL_QUANTITY', 'severity': 'warning',
         'message': 'Верхняя оценка при нулевом inbound.', 'affected_skus': [item['sku']],
         'source_reference': 'decision-core-1'},
    ])
    detail['item'] = deepcopy(item)
    result['response']['summary'] = summary(result['response']['items'])
    checked = run(result)['response']['items'][0]
    assert checked['recommended_quantity'] == 144
    assert checked['final_quantity'] is None
