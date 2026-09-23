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
