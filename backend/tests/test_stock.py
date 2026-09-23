"""HTTP/SQLite stock invariants with explicit synthetic snapshots, no live ERP."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

from fastapi.testclient import TestClient
import pytest

from backend.app.main import create_app
from backend.app.stock import validate_stock


STAMP = '2026-09-23T07:00:00Z'
URL = '/api/v1/stock-monitoring/almaty/SYNTHETIC-SKU'


@pytest.fixture(autouse=True)
def isolated_legacy_mode(monkeypatch):
    monkeypatch.setenv('TIREK_AUTH_DISABLED', '1')


def initial(**updates):
    return {'sku': 'SYNTHETIC-SKU', 'warehouse_id': 'almaty', 'unit': 'шт', 'mode': 'scenario',
            'on_hand': 60, 'reserved': 0, 'evaluated_at': STAMP,
            'policy': {'reference_stock': 100, 'reference_kind': 'manual', 'reference_id': 'fixture-target',
                       'source_kind': 'synthetic', 'policy_version': 'fixture-v1',
                       'thresholds': {'warning_pct': 40, 'high_pct': 30, 'critical_pct': 20, 'hysteresis_pp': 3}},
            **updates}


def event(revision=1, event_id='sale-1', on_hand=39, **updates):
    return {'event_id': event_id, 'sku': 'SYNTHETIC-SKU', 'warehouse_id': 'almaty', 'unit': 'шт',
            'expected_revision': revision, 'occurred_at': STAMP, 'event_type': 'sale',
            'on_hand': on_hand, 'reserved': 0, 'source_reference': 'synthetic-test', **updates}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('DATA_DIR', str(tmp_path))
    with TestClient(create_app(tmp_path / 'stock.sqlite3')) as client:
        yield client


def setup_state(client, **updates):
    response = client.post('/api/v1/stock-monitoring', json=initial(**updates))
    assert response.status_code == 201, response.text
    validate_stock('State', response.json())
    return response.json()


def records(client, kind):
    store = client.app.state.store
    with store.transaction() as db:
        return store.all(db, kind)


def post(client, payload):
    response = client.post('/api/v1/stock-events', json=payload)
    assert response.status_code == 200, response.text
    validate_stock('EventResult', response.json())
    return response.json()


def test_no_implicit_stock_from_recommendations_and_explicit_initialization(client):
    missing = client.get(URL)
    assert missing.status_code == 404
    validate_stock('Error', missing.json())
    assert records(client, 'stock_state') == []
    state = setup_state(client)
    assert state['remaining_pct'] == 60
    assert client.get(URL).json() == state
    duplicate = client.post('/api/v1/stock-monitoring', json=initial(on_hand=999))
    assert duplicate.status_code == 409
    assert client.get(URL).json() == state
    assert records(client, 'stock_transition') == []
    assert records(client, 'stock_recalculation') == []


def test_thresholds_hysteresis_fixed_reference_and_pending_intents(client):
    setup_state(client)
    for index, (qty, observed, active) in enumerate([
            (40, 'warning', 'warning'), (30, 'high', 'high'), (20, 'critical', 'critical'),
            (22, 'high', 'critical'), (23, 'high', 'high'), (33, 'warning', 'warning'), (43, 'normal', 'normal')]):
        result = post(client, event(index + 1, f'e-{index}', qty))
        assert result['state']['observed_level'] == observed
        assert result['state']['active_level'] == active
        assert result['state']['policy']['reference_stock'] == 100
        assert result['recalculation_requested'] == (index < 3)
        assert (result['transition'] is None) == (qty == 22)
    queue = records(client, 'stock_recalculation')
    assert len(queue) == 3
    assert all(q['status'] == 'pending' and q['calculation_id'] is None for q in queue)
    assert len(records(client, 'stock_transition')) == 6
    assert records(client, 'approval') == []


def test_large_drop_stockout_recovery_and_reserve_deducted_once(client):
    setup_state(client, on_hand=60, reserved=20)
    result = post(client, event(on_hand=50, reserved=10))
    assert result['state']['free_stock'] == 40
    result = post(client, event(2, 'zero', 10, reserved=10))
    assert result['state']['active_level'] == 'stockout'
    assert result['transition']['to_level'] == 'stockout'
    result = post(client, event(3, 'receipt', 250, event_type='receipt'))
    assert result['state']['remaining_pct'] == 250
    assert result['state']['active_level'] == 'normal'


def test_duplicate_is_original_result_and_never_rolls_back_or_requeues(client):
    setup_state(client)
    first = event(occurred_at='2026-09-23T07:01:00Z')
    original = post(client, first)
    current = post(client, event(2, 'next', 15, occurred_at='2026-09-23T07:02:00Z'))['state']
    replay = post(client, first)
    assert replay == {**original, 'deduplicated': True}
    assert client.get(URL).json() == current
    assert len(records(client, 'stock_event')) == 2
    assert len(records(client, 'stock_recalculation')) == 2
    assert len(records(client, 'stock_transition')) == 2
    assert client.post('/api/v1/stock-events', json={**first, 'on_hand': 35}).status_code == 409
    setup_state(client, sku='OTHER')
    # event_id uniqueness also covers a different SKU/warehouse.
    assert client.post('/api/v1/stock-events', json={**first, 'sku': 'OTHER'}).status_code == 409


def test_stale_revision_and_current_revision_with_old_timestamp_fail_atomically(client):
    setup_state(client)
    current = post(client, event(occurred_at='2026-09-23T07:01:00Z'))['state']
    for payload in (event(1, 'wrong-revision', 10), event(2, 'old-time', 10)):
        response = client.post('/api/v1/stock-events', json=payload)
        assert response.status_code == 409
        validate_stock('Error', response.json())
        assert client.get(URL).json() == current
    assert len(records(client, 'stock_event')) == 1
    assert len(records(client, 'stock_recalculation')) == 1
    equal = event(2, 'same-instant', 30, occurred_at='2026-09-23T12:01:00+05:00')
    assert post(client, equal)['state']['revision'] == 3


def test_policy_update_resets_hysteresis_and_audits_without_fake_sale(client, monkeypatch):
    setup_state(client, on_hand=19)
    state = post(client, event(on_hand=22, event_type='receipt'))['state']
    assert state['active_level'] == 'critical'
    monkeypatch.setattr('backend.app.stock.now', lambda: '2026-09-23T07:01:00Z')
    new_policy = {**state['policy'], 'policy_version': 'fixture-v2'}
    response = client.put(URL + '/policy', json={
        'expected_revision': 2, 'policy': new_policy, 'reason': 'Подтверждённая индивидуальная политика'})
    assert response.status_code == 200, response.text
    result = response.json()
    validate_stock('EventResult', result)
    assert result['state']['active_level'] == 'high'
    assert result['transition']['cause'] == 'policy_changed'
    assert result['transition']['event_id'] is None
    assert result['recalculation_requested'] is True
    audit = records(client, 'stock_policy_audit')[0]
    assert audit['previous_policy'] == state['policy']
    assert audit['new_policy'] == new_policy
    assert len(records(client, 'stock_event')) == 1
    assert len(records(client, 'stock_recalculation')) == 1
    assert client.put(URL + '/policy', json={
        'expected_revision': 2, 'policy': new_policy, 'reason': 'stale'}).status_code == 409


def test_policy_clock_rollback_rejected_without_audit_or_queue(client, monkeypatch):
    state = setup_state(client)
    monkeypatch.setattr('backend.app.stock.now', lambda: '2026-09-23T06:59:59Z')
    response = client.put(URL + '/policy', json={
        'expected_revision': 1, 'policy': {**state['policy'], 'policy_version': 'fixture-v2'}, 'reason': 'old clock'})
    assert response.status_code == 409
    assert client.get(URL).json() == state
    assert records(client, 'stock_policy_audit') == []
    assert records(client, 'stock_recalculation') == []


@pytest.mark.parametrize('updates', [
    {'on_hand': True}, {'on_hand': -1}, {'reserved': 999}, {'unit': 'м'}, {'delta': -21},
    {'occurred_at': '2026-09-23T08:00:00'}, {'expected_revision': True}, {'event_type': 'policy_changed'}])
def test_malformed_events_never_mutate_state(client, updates):
    state = setup_state(client)
    response = client.post('/api/v1/stock-events', json=event(**updates))
    assert response.status_code == 422
    validate_stock('Error', response.json())
    assert client.get(URL).json() == state
    assert records(client, 'stock_event') == []


@pytest.mark.parametrize('body', ['{', '[]', 'null', '{"on_hand":NaN}', '{"on_hand":Infinity}'])
def test_invalid_json_nonfinite_payloads_use_stock_error_contract(client, body):
    response = client.post('/api/v1/stock-events', content=body, headers={'Content-Type': 'application/json'})
    assert response.status_code == 422
    validate_stock('Error', response.json())


def test_missing_reference_zero_stockout_and_contradictory_accounting_unknown(client):
    policy = initial()['policy']
    policy.update(reference_stock=None, reference_kind='unavailable', reference_id=None)
    state = setup_state(client, on_hand=0, policy=policy)
    assert state['active_level'] == 'stockout'
    assert state['remaining_pct'] is None
    state = setup_state(client, sku='UNKNOWN', on_hand=10, reserved=20)
    assert state['active_level'] == 'unknown'
    assert state['free_stock'] is None
    assert state['remaining_pct'] is None
    invalid = initial(sku='INVALID', mode='operational')
    assert client.post('/api/v1/stock-monitoring', json=invalid).status_code == 422


def test_concurrent_writes_accept_only_one_revision(client):
    setup_state(client)
    def submit(index):
        return client.post('/api/v1/stock-events', json=event(event_id=f'parallel-{index}', on_hand=30 - index))
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(submit, range(2)))
    assert sorted(r.status_code for r in responses) == [200, 409]
    assert client.get(URL).json()['revision'] == 2
    assert len(records(client, 'stock_event')) == 1
    assert len(records(client, 'stock_transition')) == 1
    assert len(records(client, 'stock_recalculation')) == 1


def test_queue_write_failure_rolls_back_all_effects(client, monkeypatch):
    state = setup_state(client)
    store = client.app.state.store
    original = store.put
    def fail_queue(db, kind, object_id, value):
        if kind == 'stock_recalculation':
            raise ValueError('Simulated queue write failure')
        return original(db, kind, object_id, value)
    monkeypatch.setattr(store, 'put', fail_queue)
    response = client.post('/api/v1/stock-events', json=event())
    assert response.status_code == 422
    assert client.get(URL).json() == state
    assert records(client, 'stock_event') == []
    assert records(client, 'stock_transition') == []
    assert records(client, 'stock_recalculation') == []


def test_stock_and_idempotence_survive_app_restart(tmp_path, monkeypatch):
    monkeypatch.setenv('DATA_DIR', str(tmp_path))
    path = tmp_path / 'persistent.sqlite3'
    with TestClient(create_app(path)) as first:
        setup_state(first)
        result = post(first, event())
    with TestClient(create_app(path)) as second:
        assert second.get(URL).json() == result['state']
        assert post(second, event())['deduplicated'] is True
        assert len(records(second, 'stock_recalculation')) == 1


def test_stock_policy_preflight_allows_put(client):
    response = client.options(URL + '/policy', headers={
        'Origin': 'http://localhost:5173', 'Access-Control-Request-Method': 'PUT'})
    assert response.status_code == 200
    assert 'PUT' in response.headers['access-control-allow-methods']
