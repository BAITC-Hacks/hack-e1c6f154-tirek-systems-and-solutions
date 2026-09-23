import csv
from io import BytesIO, StringIO
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from backend.app.contracts import validate
from backend.app.main import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('DATA_DIR', str(tmp_path))
    monkeypatch.delenv('TIREK_PIPELINE', raising=False)
    monkeypatch.delenv('TIREK_INGESTOR', raising=False)
    with TestClient(create_app(tmp_path / 'test.sqlite3')) as client:
        yield client


def headers(key=None):
    return {'Idempotency-Key': key or str(uuid4())}


def calc_request(**updates):
    return {'dataset_id': 'demo-systeme-v1', 'as_of_date': '2026-09-22', 'warehouse_ids': ['almaty'],
            'category_codes': [], 'horizon_days': 28, 'lead_time_days': 7, 'review_period_days': 21,
            'mode': 'scenario', 'category_policies': [], 'economic_profiles': [], 'growth_adjustments': [],
            'budget_kzt': None, 'request_ai_review': False, **updates}


def create_calculation(client, **updates):
    response = client.post('/api/v1/calculations', json=calc_request(**updates), headers=headers())
    assert response.status_code == 202, response.text
    job = client.get('/api/v1/jobs/' + response.json()['job_id']).json()
    assert job['status'] == 'succeeded', job
    return job['resource_id']


def recommendations(client, calculation_id='demo-calc-001'):
    response = client.get(f'/api/v1/calculations/{calculation_id}/recommendations')
    assert response.status_code == 200, response.text
    return response.json()


def test_shared_response_contracts_and_null_semantics(client):
    validate('Health', client.get('/api/v1/health').json())
    validate('DatasetReport', client.get('/api/v1/datasets/demo-systeme-v1').json())
    result = recommendations(client)
    validate('RecommendationsResponse', result)
    assert len(result['items']) == 12
    assert result['summary']['order_cost_complete'] is False
    assert any(i['final_quantity'] == 0 for i in result['items'])
    assert any(i['final_quantity'] is None for i in result['items'])
    for item in result['items']:
        detail = client.get('/api/v1/calculations/demo-calc-001/items/' + item['item_id']).json()
        validate('ItemDetail', detail)
        assert item['forecast']['method'] == 'contract_example'
        assert item['ai']['status'] == 'not_requested'


def test_override_revision_and_immutable_csv(client):
    url = '/api/v1/calculations/demo-calc-001'
    item_id = recommendations(client)['items'][0]['item_id']
    change = {'expected_revision': 1, 'final_quantity': 156, 'reason': '=SUM(A1:A9)'}
    edited = client.patch(url + '/items/' + item_id, json=change)
    assert edited.status_code == 200, edited.text
    assert edited.json()['item']['recommended_quantity'] == 144
    assert edited.json()['meta']['revision'] == 2
    stale = client.patch(url + '/items/' + item_id, json=change)
    assert stale.status_code == 409
    validate('Error', stale.json())
    approval_request = {'expected_revision': 2, 'selected_item_ids': [item_id], 'acknowledged_issue_codes': []}
    key = headers()
    approved = client.post(url + '/approve', json=approval_request, headers=key)
    assert approved.status_code == 201, approved.text
    validate('Approval', approved.json())
    export_url = approved.json()['export_url']
    before = client.get(export_url)
    assert before.content.startswith(b'\xef\xbb\xbf')
    rows = list(csv.DictReader(StringIO(before.content.decode('utf-8-sig')), delimiter=';'))
    assert rows[0]['final_quantity'] == '156'
    assert rows[0]['override_reason'].startswith("'=")
    assert rows[0]['mode'] == 'scenario'
    assert client.patch(url + '/items/' + item_id, json={**change, 'expected_revision': 2, 'final_quantity': 168}).status_code == 200
    assert client.get(export_url).content == before.content
    assert client.post(url + '/approve', json=approval_request, headers=key).json() == approved.json()


def test_quantity_rules_missing_inputs_and_whitespace(client):
    items = recommendations(client)['items']
    url = '/api/v1/calculations/demo-calc-001/items/'
    for quantity in (-1, 13, 12.5):
        response = client.patch(url + items[0]['item_id'], json={'expected_revision': 1, 'final_quantity': quantity, 'reason': 'Проверка партии'})
        assert response.status_code == 422
    assert client.patch(url + items[0]['item_id'], json={'expected_revision': 1, 'final_quantity': 12, 'reason': '   '}).status_code == 422
    missing = next(i for i in items if i['decision_status'] == 'needs_data')
    assert client.patch(url + missing['item_id'], json={'expected_revision': 1, 'final_quantity': 12, 'reason': 'Нельзя обойти нехватку данных'}).status_code == 422
    response = client.patch(url + items[0]['item_id'], json={'expected_revision': 1, 'final_quantity': None, 'reason': 'Исключить из текущего заказа'})
    assert response.status_code == 200
    assert response.json()['item']['final_quantity'] is None


def test_budget_unknown_prices_and_review_acknowledgement(client):
    calculation_id = create_calculation(client, budget_kzt=1000)
    items = recommendations(client, calculation_id)['items']
    url = f'/api/v1/calculations/{calculation_id}/approve'
    payload = {'expected_revision': 1, 'selected_item_ids': [items[0]['item_id']], 'acknowledged_issue_codes': []}
    assert client.post(url, json=payload, headers=headers()).json()['error']['code'] == 'BUDGET_EXCEEDED'
    unknown = next(i for i in items if i['unit_cost_kzt'] is None)
    assert client.post(url, json={**payload, 'selected_item_ids': [unknown['item_id']]}, headers=headers()).json()['error']['code'] == 'PRICE_REQUIRED_FOR_BUDGET'
    review = next(i for i in items if i['decision_status'] == 'needs_review')
    url = '/api/v1/calculations/demo-calc-001/approve'
    payload['selected_item_ids'] = [review['item_id']]
    assert client.post(url, json=payload, headers=headers()).json()['error']['code'] == 'REVIEW_REQUIRED'
    payload['acknowledged_issue_codes'] = [issue['code'] for issue in review['issues']]
    assert client.post(url, json=payload, headers=headers()).status_code == 201


def test_idempotency_and_synthetic_mode(client):
    key = headers()
    first = client.post('/api/v1/calculations', json=calc_request(), headers=key)
    second = client.post('/api/v1/calculations', json=calc_request(), headers=key)
    assert first.json()['job_id'] == second.json()['job_id']
    validate('Job', second.json())
    conflict = client.post('/api/v1/calculations', json=calc_request(budget_kzt=1), headers=key)
    assert conflict.status_code == 409
    assert client.post('/api/v1/calculations', json=calc_request(mode='operational'), headers=headers()).status_code == 422
    assert client.post('/api/v1/calculations', json=calc_request(horizon_days=14), headers=headers()).status_code == 422


def test_upload_rejects_invalid_and_preserves_inspection_limits(client):
    invalid = client.post('/api/v1/datasets/import', data={'supplier_id': 'systeme-electric'}, files={'files': ('moq.xlsx', b'not an excel')}, headers=headers())
    assert invalid.status_code == 202
    job = client.get('/api/v1/jobs/' + invalid.json()['job_id']).json()
    assert job['status'] == 'failed'
    validate('Job', job)
    workbook = Workbook()
    workbook.active.append(['sku', 'quantity'])
    workbook.active.append(['000123', 12])
    buffer = BytesIO()
    workbook.save(buffer)
    def upload():
        return client.post('/api/v1/datasets/import', data={'supplier_id': 'systeme-electric'}, files={'files': ('moq.xlsx', buffer.getvalue())}, headers=headers())
    first = upload()
    first_job = client.get('/api/v1/jobs/' + first.json()['job_id']).json()
    assert first_job['status'] == 'succeeded'
    report = client.get('/api/v1/datasets/' + first_job['resource_id']).json()
    validate('DatasetReport', report)
    assert report['calculation_allowed'] is False
    assert report['sources'][0]['rows_read'] == 2
    assert report['sources'][0]['rows_used'] == 0
    second = upload()
    assert client.get('/api/v1/jobs/' + second.json()['job_id']).json()['resource_id'] == first_job['resource_id']


def test_persistence_and_interrupted_job(tmp_path, monkeypatch):
    monkeypatch.setenv('DATA_DIR', str(tmp_path))
    path = tmp_path / 'persistent.sqlite3'
    app = create_app(path)
    with TestClient(app) as client:
        request = {'expected_revision': 1, 'selected_item_ids': ['se-ez9f34116'], 'acknowledged_issue_codes': []}
        approval = client.post('/api/v1/calculations/demo-calc-001/approve', json=request, headers=headers()).json()
        with app.state.store.transaction() as db:
            app.state.store.put(db, 'job', 'interrupted', {'job_id': 'interrupted', 'kind': 'calculation', 'status': 'running', 'stage': 'Work',
                'progress_pct': 10, 'resource_type': None, 'resource_id': None, 'error': None, 'created_at': '2026-09-23T00:00:00Z', 'updated_at': '2026-09-23T00:00:00Z'})
    with TestClient(create_app(path)) as client:
        assert client.get(approval['export_url']).status_code == 200
        assert client.get('/api/v1/jobs/interrupted').json()['status'] == 'failed'
