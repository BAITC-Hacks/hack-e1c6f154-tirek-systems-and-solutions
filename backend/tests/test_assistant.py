from fastapi.testclient import TestClient
import httpx
import pytest

from backend.app import assistant
from backend.app.main import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('DATA_DIR', str(tmp_path))
    monkeypatch.setenv('TIREK_AUTH_DISABLED', '1')
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    monkeypatch.delenv('TIREK_ASSISTANT_MODEL', raising=False)
    with TestClient(create_app(tmp_path / 'assistant.sqlite3')) as value:
        yield value


def test_local_guidance_and_no_mutation(client):
    before = client.get('/api/v1/calculations/demo-calc-001/recommendations').json()
    response = client.post('/api/v1/assistant/chat', json={
        'message': 'Почему столько?', 'calculation_id': 'demo-calc-001',
        'item_id': before['items'][0]['item_id'],
    })
    assert response.status_code == 200
    assert response.json()['mode'] == 'local'
    assert before['items'][0]['sku'] in response.json()['answer']
    assert client.get('/api/v1/calculations/demo-calc-001/recommendations').json() == before
    status = client.get('/api/v1/assistant/status').json()
    assert status['available'] is False and 'key' not in status


def test_bounded_inputs_and_missing_context(client):
    assert client.post('/api/v1/assistant/chat', json={'message': ' '}).status_code == 422
    assert client.post('/api/v1/assistant/chat', json={'message': 'x' * 2001}).status_code == 422
    assert client.post('/api/v1/assistant/chat', json={'message': 'help', 'calculation_id': 'missing'}).status_code == 404
    assert client.post('/api/v1/assistant/chat', json={'message': 'help', 'item_id': 'missing'}).status_code == 422


def test_provider_consent_and_minimal_data(monkeypatch):
    requests = []
    class Provider:
        def __init__(self, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def post(self, url, **kwargs):
            requests.append(kwargs['json'])
            return httpx.Response(200, request=httpx.Request('POST', url), json={
                'status': 'completed', 'output': [{'type': 'message', 'content': [
                    {'type': 'output_text', 'text': 'Проверьте данные.'}]}]})
    monkeypatch.setattr(assistant.httpx, 'Client', Provider)
    context = {'item': {'sku': 'SENSITIVE-SKU'}}
    config = {'key': 'test-only', 'model': 'configured-model'}
    payload = assistant.ChatRequest(message='Помоги', history=[assistant.Turn(role='assistant', content='PRIVATE-OLD-CONTEXT')])
    assert assistant.remote_answer(payload, context, config)['mode'] == 'openai'
    assert requests[-1]['store'] is False
    assert 'SENSITIVE' not in str(requests[-1]) and 'PRIVATE-OLD' not in str(requests[-1])
    payload.share_context = True
    assistant.remote_answer(payload, context, config)
    assert 'SENSITIVE-SKU' in str(requests[-1])


def test_provider_error_is_not_a_fake_ai_answer(monkeypatch):
    class Offline:
        def __init__(self, **kwargs):
            pass
        def __enter__(self):
            raise httpx.ConnectError('unavailable')
        def __exit__(self, *args):
            pass
    monkeypatch.setattr(assistant.httpx, 'Client', Offline)
    with pytest.raises(assistant.DomainError) as caught:
        assistant.remote_answer(assistant.ChatRequest(message='help'), None, {'key': 'test-only', 'model': 'm'})
    assert caught.value.code == 'ASSISTANT_UNAVAILABLE'
