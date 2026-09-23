from copy import deepcopy
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app import ai_review
from backend.app.contracts import validate
from backend.app.demo import make_demo
from backend.app.main import create_app


@pytest.fixture(autouse=True)
def no_real_provider(monkeypatch):
    monkeypatch.setenv('TIREK_AUTH_DISABLED', '1')
    for key in ('TIREK_LLM_BASE_URL', 'TIREK_LLM_MODEL', 'TIREK_LLM_API_KEY'):
        monkeypatch.delenv(key, raising=False)


def detail():
    return next(iter(make_demo()['details'].values()))


def provider(monkeypatch, handler):
    monkeypatch.setenv('TIREK_LLM_BASE_URL', 'https://test-provider.invalid/v1')
    monkeypatch.setenv('TIREK_LLM_MODEL', 'test-model')
    monkeypatch.setenv('TIREK_LLM_API_KEY', 'test-only-not-a-secret')
    client = httpx.Client
    monkeypatch.setattr(ai_review.httpx, 'Client', lambda **options: client(transport=httpx.MockTransport(handler), **options))


def response(request, **changes):
    body = json.loads(request.content)
    context = json.loads(body['messages'][1]['content'])
    judgement = {'status': 'reviewed', 'verdict': 'needs_review', 'reasons': ['Проверьте сроки поставки.'],
                 'evidence_ids': [context['evidence'][0]['id']], 'rule_ids': ['AI-01'],
                 'suggested_action': 'Проверить сроки поставки', 'provider_model': 'test-model', **changes}
    return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(judgement)}}]})


def test_without_provider_never_claims_ai_review():
    result = ai_review.review(detail())
    validate('AIJudgement', result)
    assert result['status'] == 'unavailable'
    assert result['provider_model'] is None


def test_provider_transport_and_grounded_reply_do_not_mutate_calculation(monkeypatch):
    provider(monkeypatch, response)
    source = detail()
    original = deepcopy(source)
    result = ai_review.review(source)
    validate('AIJudgement', result)
    assert result['status'] == 'reviewed'
    assert result['provider_model'] == 'test-model'
    assert source == original


def test_fabricated_evidence_and_auth_errors_stay_unavailable(monkeypatch):
    provider(monkeypatch, lambda request: response(request, evidence_ids=['invented']))
    assert ai_review.review(detail())['status'] == 'unavailable'


def test_network_failure_retries_only_once(monkeypatch):
    calls = []
    def failed(request):
        calls.append(request)
        raise httpx.ConnectError('test transport error')
    provider(monkeypatch, failed)
    assert ai_review.review(detail())['status'] == 'unavailable'
    assert len(calls) == 2


def test_ai_endpoint_persists_only_judgement_and_rejects_stale_revision(tmp_path, monkeypatch):
    monkeypatch.setenv('DATA_DIR', str(tmp_path))
    app = create_app(tmp_path / 'test.sqlite3')
    with TestClient(app) as client:
        item = client.get('/api/v1/calculations/demo-calc-001/recommendations').json()['items'][0]
        path = '/api/v1/calculations/demo-calc-001/items/' + item['item_id']
        reply = client.post(path + '/ai-review', json={'expected_revision': 1})
        assert reply.status_code == 200
        assert reply.json()['item']['ai']['status'] == 'unavailable'
        assert reply.json()['item']['final_quantity'] == item['final_quantity']
        assert client.get(path).json() == reply.json()
        assert client.post(path + '/ai-review', json={'expected_revision': 999}).status_code == 409


def test_revision_changed_during_provider_call_discards_result(tmp_path, monkeypatch):
    monkeypatch.setenv('DATA_DIR', str(tmp_path))
    app = create_app(tmp_path / 'test.sqlite3')
    def concurrent_edit(source):
        with app.state.store.transaction() as db:
            value = app.state.store.get(db, 'calculation', 'demo-calc-001')
            value['response']['meta']['revision'] = 2
            app.state.store.put(db, 'calculation', 'demo-calc-001', value)
        return ai_review.unavailable('Test only')
    monkeypatch.setattr('backend.app.main.review_ai', concurrent_edit)
    with TestClient(app) as client:
        item = client.get('/api/v1/calculations/demo-calc-001/recommendations').json()['items'][0]
        path = '/api/v1/calculations/demo-calc-001/items/' + item['item_id']
        assert client.post(path + '/ai-review', json={'expected_revision': 1}).status_code == 409
        assert client.get(path).json()['item']['ai'] == item['ai']


def test_single_and_batch_ai_review_share_account_quota_without_charging_retries(tmp_path, monkeypatch):
    monkeypatch.setenv('DATA_DIR', str(tmp_path))
    monkeypatch.delenv('TIREK_AUTH_DISABLED')
    monkeypatch.setattr('backend.app.main.ai_configuration', lambda: {'available': True})
    calls = []

    def mocked_review(source):
        calls.append(source['item']['item_id'])
        return ai_review.unavailable('Mock provider, no external requests')

    monkeypatch.setattr('backend.app.main.review_ai', mocked_review)
    monkeypatch.setattr('backend.app.ai_review.review', mocked_review)
    # The built-in demo explicitly marks requested reviews unavailable. Use its
    # deterministic rows as fresh candidates, as a newly calculated upload would.
    # Keep the real batch-review function and quota admission in this test.
    monkeypatch.setattr('backend.app.pipeline.make_demo', lambda calculation_id, request:
                        make_demo(calculation_id, {**request, 'request_ai_review': False}))
    app = create_app(tmp_path / 'ai-quota.sqlite3')
    payload = {'dataset_id': 'demo-systeme-v1', 'as_of_date': '2026-09-22', 'warehouse_ids': ['almaty'],
               'category_codes': [], 'horizon_days': 28, 'lead_time_days': 7, 'review_period_days': 21,
               'mode': 'scenario', 'category_policies': [], 'economic_profiles': [], 'growth_adjustments': [],
               'budget_kzt': None, 'request_ai_review': True}
    path = '/api/v1/calculations/demo-calc-001/items/se-ez9f34116/ai-review'
    with TestClient(app) as first, TestClient(app) as second:
        for client, email in ((first, 'first@example.test'), (second, 'second@example.test')):
            registered = client.post('/api/v1/auth/register', json={
                'email': email, 'name': 'Quota test', 'workspace_name': 'Private', 'password': 'Synthetic password 123!'})
            assert registered.status_code == 201
            client.headers['X-CSRF-Token'] = registered.json()['csrf_token']
        # 1 direct review + 5 reserved batch items + 4 direct = 10 per minute.
        assert first.post(path, json={'expected_revision': 1}).status_code == 200
        created = first.post('/api/v1/calculations', json=payload, headers={'Idempotency-Key': 'batch-one'})
        assert created.status_code == 202
        job = first.get('/api/v1/jobs/' + created.json()['job_id']).json()
        assert job['status'] == 'succeeded', job
        assert len(calls) == 6
        replayed = first.post('/api/v1/calculations', json=payload, headers={'Idempotency-Key': 'batch-one'})
        assert replayed.status_code == 202 and replayed.json()['job_id'] == created.json()['job_id']
        assert len(calls) == 6
        for _ in range(4):
            assert first.post(path, json={'expected_revision': 1}).status_code == 200
        assert first.post(path, json={'expected_revision': 1}).status_code == 429
        rejected = first.post('/api/v1/calculations', json=payload, headers={'Idempotency-Key': 'batch-two'})
        assert rejected.status_code == 429
        assert len(calls) == 10
        assert len(first.get('/api/v1/workspace').json()['calculations']) == 2
        assert second.post(path, json={'expected_revision': 1}).status_code == 200
        # No external service means no paid quota: normal calculations still run.
        payload['request_ai_review'] = False
        assert first.post('/api/v1/calculations', json=payload, headers={'Idempotency-Key': 'no-ai'}).status_code == 202
