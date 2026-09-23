import csv
from io import BytesIO, StringIO
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from backend.app.contracts import validate
from backend.app.main import create_app


@pytest.fixture(autouse=True)
def legacy_local_mode(monkeypatch):
    monkeypatch.setenv('TIREK_AUTH_DISABLED', '1')


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
    assert client.post(url, json=payload, headers=headers()).json()['error']['code'] == 'UNCONFIRMED_CONSTRAINTS'
    payload['acknowledged_issue_codes'] = [issue['code'] for issue in review['issues']]
    assert client.post(url, json=payload, headers=headers()).json()['error']['code'] == 'UNCONFIRMED_CONSTRAINTS'


def _set_saved_constraints(client, item_id, **changes):
    """Persist only synthetic server-owned sidecars, matching ml_adapter's format."""
    store = client.app.state.store
    with store.transaction() as db:
        result = store.get(db, 'calculation', 'demo-calc-001')
        result['approval_constraints'] = {
            key: {'unit_quantum': 1, 'min_order_qty': value['item']['min_order_qty'],
                  'order_multiple': value['item']['order_multiple'], 'minimum_safe_quantity': 0,
                  'constraints_complete': value['item']['min_order_qty'] is not None and value['item']['order_multiple'] is not None}
            for key, value in result['details'].items()
        }
        result['approval_constraints'][item_id].update(changes)
        store.put(db, 'calculation', 'demo-calc-001', result)


def test_model_floor_is_checked_on_override_and_again_on_approval(client):
    url = '/api/v1/calculations/demo-calc-001'
    item_id = recommendations(client)['items'][0]['item_id']
    _set_saved_constraints(client, item_id, minimum_safe_quantity=48)
    for quantity in (0, 36):
        response = client.patch(url + '/items/' + item_id, json={
            'expected_revision': 1, 'final_quantity': quantity, 'reason': 'Проверка обязательного минимума'})
        assert response.status_code == 422
        assert response.json()['error']['code'] == 'MINIMUM_QUANTITY_VIOLATION'
    assert recommendations(client)['meta']['revision'] == 1
    # Simulate a stored legacy/incorrect provider value. Approval must repeat
    # the guard instead of assuming that every record came through PATCH.
    store = client.app.state.store
    with store.transaction() as db:
        result = store.get(db, 'calculation', 'demo-calc-001')
        result['details'][item_id]['item']['final_quantity'] = 36
        store.put(db, 'calculation', 'demo-calc-001', result)
    rejected = client.post(url + '/approve', json={
        'expected_revision': 1, 'selected_item_ids': [item_id], 'acknowledged_issue_codes': []}, headers=headers())
    assert rejected.json()['error']['code'] == 'MINIMUM_QUANTITY_VIOLATION'
    accepted = client.patch(url + '/items/' + item_id, json={
        'expected_revision': 1, 'final_quantity': 48, 'reason': 'Соблюдаем подтверждённый минимум'})
    assert accepted.status_code == 200, accepted.text
    approved = client.post(url + '/approve', json={
        'expected_revision': 2, 'selected_item_ids': [item_id], 'acknowledged_issue_codes': []}, headers=headers())
    assert approved.status_code == 201, approved.text


def test_incomplete_constraints_cannot_be_overridden_even_with_acknowledgement(client):
    items = recommendations(client)['items']
    review = next(item for item in items if item['decision_status'] == 'needs_review')
    url = '/api/v1/calculations/demo-calc-001'
    _set_saved_constraints(client, review['item_id'])
    for quantity in (0, 12):
        edited = client.patch(url + '/items/' + review['item_id'], json={
            'expected_revision': 1, 'final_quantity': quantity, 'reason': 'Текст не заменяет условия'})
        assert edited.json()['error']['code'] == 'UNCONFIRMED_CONSTRAINTS'
    approved = client.post(url + '/approve', json={
        'expected_revision': 1, 'selected_item_ids': [review['item_id']],
        'acknowledged_issue_codes': [issue['code'] for issue in review['issues']]}, headers=headers())
    assert approved.json()['error']['code'] == 'UNCONFIRMED_CONSTRAINTS'
    excluded = client.patch(url + '/items/' + review['item_id'], json={
        'expected_revision': 1, 'final_quantity': None, 'reason': 'Исключить до уточнения условий'})
    assert excluded.status_code == 200


def test_manager_can_explicitly_confirm_missing_supplier_constraints_in_scenario(client):
    review = next(item for item in recommendations(client)['items']
                  if item['decision_status'] == 'needs_review')
    _set_saved_constraints(client, review['item_id'])
    url = '/api/v1/calculations/demo-calc-001'
    confirmation = {
        'unit_quantum': 1, 'min_order_qty': 0, 'order_multiple': 1,
        'warehouse_scope_confirmed': True,
        'source_reference': 'Менеджер сверил карточку поставщика SUP-42',
    }
    edited = client.patch(url + '/items/' + review['item_id'], json={
        'expected_revision': 1, 'final_quantity': 12,
        'reason': 'Условия поставки подтверждены ответственным менеджером',
        'constraint_confirmation': confirmation,
    })
    assert edited.status_code == 200, edited.text
    item = edited.json()['item']
    assert item['min_order_qty'] == 0
    assert item['order_multiple'] == 1
    assert any(issue['code'] == 'MANUAL_CONSTRAINT_CONFIRMATION' for issue in item['issues'])
    store = client.app.state.store
    with store.transaction() as db:
        saved = store.get(db, 'calculation', 'demo-calc-001')
    assert saved['approval_constraints'][review['item_id']]['constraints_complete'] is True
    assert saved['constraint_confirmations'][review['item_id']]['source_reference'] == confirmation['source_reference']
    issue_codes = [issue['code'] for issue in item['issues']]
    approved = client.post(url + '/approve', json={
        'expected_revision': 2, 'selected_item_ids': [review['item_id']],
        'acknowledged_issue_codes': issue_codes,
    }, headers=headers())
    assert approved.status_code == 201, approved.text


def test_manager_can_confirm_zero_inbound_and_constraints_without_silent_default(client):
    item = recommendations(client)['items'][0]
    store = client.app.state.store
    with store.transaction() as db:
        result = store.get(db, 'calculation', 'demo-calc-001')
        saved = result['details'][item['item_id']]['item']
        saved.update(decision_status='needs_data', final_quantity=None, inbound_within_horizon=None)
        saved['issues'].append({
            'code': 'REQUIRED_INBOUND_QUANTITY', 'severity': 'error',
            'message': 'Нужно уточнить: inbound_quantity', 'affected_skus': [saved['sku']],
            'source_reference': None,
        })
        store.put(db, 'calculation', 'demo-calc-001', result)
    _set_saved_constraints(client, item['item_id'], min_order_qty=None, constraints_complete=False)
    response = client.patch('/api/v1/calculations/demo-calc-001/items/' + item['item_id'], json={
        'expected_revision': 1, 'final_quantity': item['recommended_quantity'],
        'reason': 'Менеджер сверил открытые поставки и карточку поставщика',
        'inbound_confirmation': {
            'no_inbound_confirmed': True, 'source_reference': 'Реестр открытых заказов на 23.09.2026',
        },
        'constraint_confirmation': {
            'unit_quantum': 1, 'min_order_qty': 0, 'order_multiple': 12,
            'warehouse_scope_confirmed': True, 'source_reference': 'Договор SUP-42',
        },
    })
    assert response.status_code == 200, response.text
    changed = response.json()['item']
    assert changed['decision_status'] == 'needs_review'
    assert changed['inbound_within_horizon'] == 0
    assert {issue['code'] for issue in changed['issues']} >= {
        'MANUAL_ZERO_INBOUND_CONFIRMATION', 'MANUAL_CONSTRAINT_CONFIRMATION'}
    with store.transaction() as db:
        result = store.get(db, 'calculation', 'demo-calc-001')
    assert result['inbound_confirmations'][item['item_id']]['no_inbound_confirmed'] is True


def test_manual_confirmation_cannot_replace_already_confirmed_supplier_constraints(client):
    item = recommendations(client)['items'][0]
    response = client.patch('/api/v1/calculations/demo-calc-001/items/' + item['item_id'], json={
        'expected_revision': 1, 'final_quantity': item['recommended_quantity'],
        'reason': 'Попытка заменить известные условия ручным значением',
        'constraint_confirmation': {
            'unit_quantum': 1, 'min_order_qty': 0, 'order_multiple': 1,
            'warehouse_scope_confirmed': True, 'source_reference': 'manual-guess',
        },
    })
    assert response.status_code == 422
    assert response.json()['error']['code'] == 'INVALID_PARAMETERS'


def test_manual_confirmation_cannot_weaken_known_quantum_or_multiple(client):
    item = recommendations(client)['items'][0]
    _set_saved_constraints(client, item['item_id'], min_order_qty=None, constraints_complete=False)
    base = {
        'expected_revision': 1, 'final_quantity': 0.1,
        'reason': 'Попытка ослабить известные ограничения',
        'constraint_confirmation': {
            'unit_quantum': 0.1, 'min_order_qty': 0, 'order_multiple': 0.1,
            'warehouse_scope_confirmed': True, 'source_reference': 'manual-guess',
        },
    }
    response = client.patch(
        '/api/v1/calculations/demo-calc-001/items/' + item['item_id'], json=base)
    assert response.status_code == 422
    assert response.json()['error']['code'] == 'INVALID_PARAMETERS'
    assert recommendations(client)['meta']['revision'] == 1


def test_saved_constraint_completeness_and_missing_model_sidecar_fail_closed(client):
    item_id = recommendations(client)['items'][0]['item_id']
    url = '/api/v1/calculations/demo-calc-001/items/' + item_id
    body = {'expected_revision': 1, 'final_quantity': 144, 'reason': 'Проверка ограничений модели'}
    _set_saved_constraints(client, item_id, constraints_complete=False)
    assert client.patch(url, json=body).json()['error']['code'] == 'UNCONFIRMED_CONSTRAINTS'
    store = client.app.state.store
    with store.transaction() as db:
        result = store.get(db, 'calculation', 'demo-calc-001')
        del result['approval_constraints']
        result['details'][item_id]['item']['forecast']['method'] = 'ml'
        store.put(db, 'calculation', 'demo-calc-001', result)
    assert client.patch(url, json=body).json()['error']['code'] == 'RECALCULATION_REQUIRED'


def test_physical_quantum_and_public_supplier_constraint_consistency(client):
    item_id = recommendations(client)['items'][0]['item_id']
    store = client.app.state.store
    with store.transaction() as db:
        result = store.get(db, 'calculation', 'demo-calc-001')
        result['details'][item_id]['item'].update(unit='м', min_order_qty=0, order_multiple=.1)
        store.put(db, 'calculation', 'demo-calc-001', result)
    _set_saved_constraints(client, item_id, unit_quantum=.1, min_order_qty=0, order_multiple=.1)
    url = '/api/v1/calculations/demo-calc-001/items/' + item_id
    body = {'expected_revision': 1, 'final_quantity': .15, 'reason': 'Дробная физическая единица'}
    assert client.patch(url, json=body).json()['error']['code'] == 'INVALID_PARAMETERS'
    assert client.patch(url, json={**body, 'final_quantity': .2}).status_code == 200
    _set_saved_constraints(client, item_id, unit_quantum=.1, min_order_qty=0, order_multiple=.2)
    assert client.patch(url, json={**body, 'expected_revision': 2, 'final_quantity': .4}).json()['error']['code'] == 'RECALCULATION_REQUIRED'


def test_revision_change_invalidates_all_reviewed_ai_but_preserves_recommendations(client):
    items = recommendations(client)['items']
    first, second = items[0]['item_id'], items[1]['item_id']
    store = client.app.state.store
    with store.transaction() as db:
        result = store.get(db, 'calculation', 'demo-calc-001')
        for item_id in (first, second):
            # Explicit MOCK; no real provider call is made in this test.
            result['details'][item_id]['item']['ai'].update(
                status='reviewed', verdict='supports', reasons=['MOCK old revision'],
                provider_model='MOCK-NOT-A-REAL-CALL')
        store.put(db, 'calculation', 'demo-calc-001', result)
    response = client.patch('/api/v1/calculations/demo-calc-001/items/' + first, json={
        'expected_revision': 1, 'final_quantity': 156, 'reason': 'Новая ревизия расчёта'})
    assert response.status_code == 200
    result = recommendations(client)
    assert result['meta']['revision'] == 2
    for item_id in (first, second):
        item = next(row for row in result['items'] if row['item_id'] == item_id)
        assert item['ai']['status'] == 'unavailable'
        assert item['ai']['verdict'] is None
        assert item['ai']['provider_model'] is None
        assert item['ai']['evidence_ids'] == []
    assert result['items'][0]['recommended_quantity'] == items[0]['recommended_quantity']
    assert result['items'][2]['ai']['status'] == 'not_requested'
    validate('RecommendationsResponse', result)


@pytest.mark.parametrize('budget,cached_cost,expected_status', [(.3, 999, 201), (.299, 0, 422)])
def test_approval_recomputes_decimal_budget_from_quantity_and_price(client, budget, cached_cost, expected_status):
    item_id = recommendations(client)['items'][0]['item_id']
    store = client.app.state.store
    with store.transaction() as db:
        result = store.get(db, 'calculation', 'demo-calc-001')
        result['request']['budget_kzt'] = budget
        result['details'][item_id]['item'].update(
            final_quantity=3, min_order_qty=0, order_multiple=1, unit_cost_kzt=.1, order_cost_kzt=cached_cost)
        store.put(db, 'calculation', 'demo-calc-001', result)
    _set_saved_constraints(client, item_id)
    response = client.post('/api/v1/calculations/demo-calc-001/approve', json={
        'expected_revision': 1, 'selected_item_ids': [item_id], 'acknowledged_issue_codes': []}, headers=headers())
    assert response.status_code == expected_status, response.text
    if expected_status == 201:
        with store.transaction() as db:
            saved = store.get(db, 'approval', response.json()['approval_id'])
        assert saved['items'][0]['order_cost_kzt'] == .3
    else:
        assert response.json()['error']['code'] == 'BUDGET_EXCEEDED'


def test_acknowledgement_still_allows_reviewable_warning_with_complete_constraints(client):
    item = recommendations(client)['items'][0]
    store = client.app.state.store
    with store.transaction() as db:
        result = store.get(db, 'calculation', 'demo-calc-001')
        target = result['details'][item['item_id']]['item']
        target['decision_status'] = 'needs_review'
        target['issues'] = [{'code': 'SHORTAGE_BEFORE_NEW_ORDER_ARRIVAL', 'severity': 'warning',
                             'message': 'Нужна проверка ускорения', 'affected_skus': [target['sku']],
                             'source_reference': 'synthetic-test'}]
        store.put(db, 'calculation', 'demo-calc-001', result)
    payload = {'expected_revision': 1, 'selected_item_ids': [item['item_id']], 'acknowledged_issue_codes': []}
    url = '/api/v1/calculations/demo-calc-001/approve'
    assert client.post(url, json=payload, headers=headers()).json()['error']['code'] == 'REVIEW_REQUIRED'
    payload['acknowledged_issue_codes'] = ['SHORTAGE_BEFORE_NEW_ORDER_ARRIVAL']
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
