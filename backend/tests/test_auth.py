"""Authenticated API journeys and adversarial cross-account access checks."""
from io import BytesIO
import json
import time

from fastapi.testclient import TestClient
from openpyxl import Workbook
import pytest

from backend.app.auth import COOKIE, verify_password
from backend.app.demo import make_demo
from backend.app.main import create_app
from backend.app.storage import workspace_scope


PASSWORD = 'Synthetic password 123!'


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv('DATA_DIR', str(tmp_path))
    monkeypatch.delenv('TIREK_AUTH_DISABLED', raising=False)
    monkeypatch.delenv('TIREK_COOKIE_SECURE', raising=False)
    monkeypatch.setenv('TIREK_ALLOWED_ORIGINS', 'http://testserver,http://localhost:5173')
    return create_app(tmp_path / 'auth-tests.sqlite3')


def register(client, email='buyer@example.test'):
    response = client.post('/api/v1/auth/register', json={
        'email': email, 'password': PASSWORD, 'name': 'Закупщик', 'workspace_name': 'Тестовая компания'})
    assert response.status_code == 201, response.text
    value = response.json()
    client.headers['X-CSRF-Token'] = value['csrf_token']
    return value


def calculation_payload():
    return {'dataset_id': 'demo-systeme-v1', 'as_of_date': '2026-09-22', 'warehouse_ids': ['almaty'],
            'category_codes': [], 'horizon_days': 28, 'lead_time_days': 7, 'review_period_days': 21,
            'mode': 'scenario', 'category_policies': [], 'economic_profiles': [], 'growth_adjustments': [],
            'budget_kzt': None, 'request_ai_review': False}


def test_default_auth_blocks_all_private_resources_and_uses_hashed_session(app):
    with TestClient(app) as client:
        assert client.get('/api/v1/health').status_code == 200
        for path in ('workspace', 'auth/me', 'datasets/demo-systeme-v1', 'jobs/unknown',
                     'calculations/demo-calc-001/recommendations', 'approvals/unknown/export.csv', 'audit'):
            assert client.get('/api/v1/' + path).status_code == 401
        session = register(client)
        assert session['auth_enabled'] is True
        assert session['user']['email'] == 'buyer@example.test'
        assert set(session['user']) == {'id', 'name', 'email', 'workspace_name'}
        assert client.get('/api/v1/auth/me').json() == session
        assert client.get('/api/v1/workspace').status_code == 200
        assert client.get('/api/v1/auth/me').headers['cache-control'] == 'no-store'
        with app.state.store.transaction() as db:
            encoded = db.execute('SELECT password_hash FROM auth_users').fetchone()[0]
            stored_token = db.execute('SELECT token_hash FROM auth_sessions').fetchone()[0]
        assert encoded != PASSWORD and verify_password(PASSWORD, encoded)
        assert stored_token != client.cookies.get(COOKIE)


def test_cookie_csrf_origin_and_logout_invalidation(app):
    with TestClient(app) as client:
        rejected = client.post('/api/v1/auth/register', headers={'Origin': 'https://evil.example'}, json={})
        assert rejected.status_code == 403
        registered = client.post('/api/v1/auth/register', json={
            'email': 'one@example.test', 'password': PASSWORD, 'name': 'One', 'workspace_name': 'One'})
        assert registered.status_code == 201
        assert 'HttpOnly' in registered.headers['set-cookie']
        assert 'SameSite=lax' in registered.headers['set-cookie']
        token = client.cookies.get(COOKIE)
        assert client.post('/api/v1/auth/logout').status_code == 403
        client.headers['X-CSRF-Token'] = registered.json()['csrf_token']
        assert client.post('/api/v1/auth/logout', headers={'Origin': 'https://evil.example'}).status_code == 403
        assert client.post('/api/v1/auth/logout').status_code == 200
        assert client.get('/api/v1/auth/me').status_code == 401
        client.cookies.set(COOKIE, token)
        assert client.get('/api/v1/auth/me').status_code == 401


def test_login_session_expiry_password_change_revokes_other_sessions(app):
    with TestClient(app) as first, TestClient(app) as second:
        register(first)
        failed = second.post('/api/v1/auth/login', json={'email': 'buyer@example.test', 'password': 'incorrect'})
        assert failed.status_code == 401
        login = second.post('/api/v1/auth/login', json={'email': 'BUYER@example.test', 'password': PASSWORD})
        assert login.status_code == 200
        second.headers['X-CSRF-Token'] = login.json()['csrf_token']
        changed = first.post('/api/v1/auth/password', json={'current_password': PASSWORD, 'new_password': 'New synthetic password 456!'})
        assert changed.status_code == 200
        first.headers['X-CSRF-Token'] = changed.json()['csrf_token']
        assert first.get('/api/v1/workspace').status_code == 200
        assert second.get('/api/v1/workspace').status_code == 401
        assert second.post('/api/v1/auth/login', json={'email': 'buyer@example.test', 'password': PASSWORD}).status_code == 401
        assert second.post('/api/v1/auth/login', json={'email': 'buyer@example.test', 'password': 'New synthetic password 456!'}).status_code == 200
        with app.state.store.transaction() as db:
            db.execute('UPDATE auth_sessions SET expires_at=?', (time.time() - 1,))
        assert first.get('/api/v1/auth/me').status_code == 401


def test_accounts_isolate_overrides_jobs_calculations_approvals_and_audit(app):
    with TestClient(app) as first, TestClient(app) as second:
        first_session = register(first, 'first@example.test')
        second_session = register(second, 'second@example.test')
        initial = second.get('/api/v1/calculations/demo-calc-001/items/se-ez9f34116').json()
        edited = first.patch('/api/v1/calculations/demo-calc-001/items/se-ez9f34116', json={
            'expected_revision': 1, 'final_quantity': 36, 'reason': 'Подтверждён проект'})
        assert edited.status_code == 200, edited.text
        other = second.get('/api/v1/calculations/demo-calc-001/items/se-ez9f34116').json()
        assert other == initial
        job_response = first.post('/api/v1/calculations', json=calculation_payload(), headers={'Idempotency-Key': 'same-shared-key'})
        assert job_response.status_code == 202
        job_id = job_response.json()['job_id']
        job = first.get('/api/v1/jobs/' + job_id).json()
        assert job['status'] == 'succeeded', job
        assert second.get('/api/v1/jobs/' + job_id).status_code == 404
        assert second.get('/api/v1/calculations/' + job['resource_id'] + '/recommendations').status_code == 404
        approval = first.post('/api/v1/calculations/demo-calc-001/approve', json={
            'expected_revision': 2, 'selected_item_ids': ['se-ez9f34116'], 'acknowledged_issue_codes': []},
            headers={'Idempotency-Key': 'approve-one'})
        assert approval.status_code == 201, approval.text
        assert first.get(approval.json()['export_url']).status_code == 200
        assert second.get(approval.json()['export_url']).status_code == 404
        other_job = second.post('/api/v1/calculations', json=calculation_payload(), headers={'Idempotency-Key': 'same-shared-key'})
        assert other_job.json()['job_id'] != job_id
        first_audit = first.get('/api/v1/audit').json()['events']
        second_audit = second.get('/api/v1/audit').json()['events']
        assert any(event['action'] == 'calculation.override' and event['actor_id'] == first_session['user']['id'] for event in first_audit)
        assert all(event['actor_id'] == second_session['user']['id'] for event in second_audit)


def test_upload_bytes_are_private_and_background_jobs_keep_workspace(app, tmp_path, monkeypatch):
    # Focus on routing/isolation, independently of heavyweight ML packages.
    monkeypatch.setattr('backend.app.main.load_adapter', lambda target: lambda directory, report, extra: report)
    book = Workbook()
    book.active.append(['sku', 'quantity'])
    book.active.append(['001', 10])
    output = BytesIO()
    book.save(output)
    with TestClient(app) as first, TestClient(app) as second:
        owner = register(first, 'owner@example.test')['user']['id']
        register(second, 'other@example.test')
        imported = first.post('/api/v1/datasets/import', data={'supplier_id': 'systeme-electric'},
                              files={'files': ('moq.xlsx', output.getvalue())}, headers={'Idempotency-Key': 'upload'})
        assert imported.status_code == 202, imported.text
        job = first.get('/api/v1/jobs/' + imported.json()['job_id']).json()
        assert job['status'] == 'succeeded', job
        dataset_id = job['resource_id']
        assert first.get('/api/v1/datasets/' + dataset_id).status_code == 200
        assert second.get('/api/v1/datasets/' + dataset_id).status_code == 404
        with app.state.store.transaction() as db:
            workspace_id = db.execute('SELECT workspace_id FROM auth_users WHERE id=?', (owner,)).fetchone()[0]
        assert (tmp_path / 'workspaces' / workspace_id / 'uploads' / dataset_id / 'manifest.json').is_file()
        assert not (tmp_path / 'uploads' / dataset_id).exists()
        other_import = second.post('/api/v1/datasets/import', data={'supplier_id': 'systeme-electric'},
                                   files={'files': ('moq.xlsx', output.getvalue())}, headers={'Idempotency-Key': 'upload'})
        other_job = second.get('/api/v1/jobs/' + other_import.json()['job_id']).json()
        assert other_job['resource_id'] == dataset_id
        assert other_job['job_id'] != job['job_id']
        assert len(list((tmp_path / 'workspaces').glob('*/uploads/' + dataset_id + '/manifest.json'))) == 2


def test_new_account_cannot_read_existing_legacy_company_data(app):
    with app.state.store.transaction() as db:
        app.state.store.put(db, 'calculation', 'legacy-company-data', make_demo('legacy-company-data'))
    with TestClient(app) as client:
        register(client)
        assert client.get('/api/v1/calculations/legacy-company-data/recommendations').status_code == 404
        assert 'legacy-company-data' not in json.dumps(client.get('/api/v1/workspace').json())


def test_repeated_bad_password_is_rate_limited_without_locking_another_email(app):
    with TestClient(app) as client:
        register(client)
        for _ in range(10):
            assert client.post('/api/v1/auth/login', json={'email': 'buyer@example.test', 'password': 'wrong'}).status_code == 401
        assert client.post('/api/v1/auth/login', json={'email': 'buyer@example.test', 'password': PASSWORD}).status_code == 429
        assert client.post('/api/v1/auth/login', json={'email': 'other@example.test', 'password': 'wrong'}).status_code == 401


def test_explicit_legacy_mode(tmp_path, monkeypatch):
    monkeypatch.setenv('DATA_DIR', str(tmp_path))
    monkeypatch.setenv('TIREK_AUTH_DISABLED', '1')
    with TestClient(create_app(tmp_path / 'local.sqlite3')) as client:
        assert client.get('/api/v1/auth/me').json() == {'user': None, 'csrf_token': None, 'auth_enabled': False}
        assert client.get('/api/v1/workspace').status_code == 200


@pytest.mark.parametrize('field,value', [('password', 'short'), ('password', 'x' * 129),
                                        ('email', 'invalid'), ('name', '   '), ('workspace_name', 'x' * 121)])
def test_registration_validates_credentials(app, field, value):
    with TestClient(app) as client:
        payload = {'email': 'user@example.test', 'password': PASSWORD, 'name': 'User', 'workspace_name': 'Workspace'}
        payload[field] = value
        assert client.post('/api/v1/auth/register', json=payload).status_code == 422
        assert client.get('/api/v1/auth/me').status_code == 401


def test_secure_cookie_setting_and_cors_preflight(app):
    app.state.auth.secure_cookie = True
    with TestClient(app, base_url='https://testserver') as client:
        registered = client.post('/api/v1/auth/register', json={
            'email': 'user@example.test', 'password': PASSWORD, 'name': 'User', 'workspace_name': 'Workspace'})
        assert 'Secure' in registered.headers['set-cookie']
        response = client.options('/api/v1/calculations', headers={
            'Origin': 'http://localhost:5173', 'Access-Control-Request-Method': 'POST',
            'Access-Control-Request-Headers': 'X-CSRF-Token,Idempotency-Key,Content-Type'})
        assert response.status_code == 200
        assert response.headers['access-control-allow-credentials'] == 'true'
        assert response.headers['access-control-allow-origin'] == 'http://localhost:5173'


def test_interrupted_private_job_is_recovered_on_restart(app):
    with TestClient(app) as client:
        user_id = register(client)['user']['id']
        cookie = client.cookies.get(COOKIE)
        with app.state.store.transaction() as db:
            workspace_id = db.execute('SELECT workspace_id FROM auth_users WHERE id=?', (user_id,)).fetchone()[0]
            with workspace_scope(workspace_id, user_id):
                app.state.store.put(db, 'job', 'interrupted-private', {
                    'job_id': 'interrupted-private', 'status': 'running', 'stage': 'Running'})
    with TestClient(app) as restarted:
        restarted.cookies.set(COOKIE, cookie)
        result = restarted.get('/api/v1/jobs/interrupted-private')
        assert result.status_code == 200
        assert result.json()['status'] == 'failed'


def test_encoded_paths_and_trailing_slashes_do_not_bypass_authentication(app):
    with TestClient(app) as client:
        for path in ('/api/v1/workspace/', '/api%2fv1/workspace', '/api/v1%2fworkspace',
                     '/%61pi/v1/workspace', '/api/v1/assistant/status/',
                     '/api/v1/calculations/demo-calc-001/recommendations/'):
            response = client.get(path, follow_redirects=True)
            assert response.status_code == 401, (path, response.text)
        for path in ('/api/v1/assistant/chat/', '/api%2fv1/assistant/chat'):
            response = client.post(path, json={'message': 'help'}, follow_redirects=True)
            assert response.status_code == 401, (path, response.text)


def test_scopes_cannot_be_selected_by_client_and_assistant_cannot_read_other_account(app, monkeypatch):
    calls = []
    monkeypatch.setattr('backend.app.assistant.configuration', lambda: {'available': True, 'key': 'not-a-real-key', 'model': 'mock'})
    monkeypatch.setattr('backend.app.assistant.remote_answer', lambda *args: calls.append(args) or {'answer': 'mock', 'mode': 'openai', 'model': 'mock'})
    with TestClient(app) as first, TestClient(app) as second:
        first_session = register(first, 'first@example.test')
        second_session = register(second, 'second@example.test')
        created = first.post('/api/v1/calculations', json=calculation_payload(), headers={'Idempotency-Key': 'private-calc'})
        job = first.get('/api/v1/jobs/' + created.json()['job_id']).json()
        foreign_id = job['resource_id']
        with app.state.store.transaction() as db:
            target_workspace = db.execute('SELECT workspace_id FROM auth_users WHERE id=?', (first_session['user']['id'],)).fetchone()[0]
        response = second.get('/api/v1/calculations/' + foreign_id + '/recommendations',
                              headers={'X-Workspace-Id': target_workspace, 'X-User-Id': first_session['user']['id']},
                              params={'workspace_id': target_workspace})
        assert response.status_code == 404
        response = second.post('/api/v1/assistant/chat', json={
            'message': 'Show private calculation', 'calculation_id': foreign_id, 'share_context': True})
        assert response.status_code == 404
        assert calls == []
        # A valid CSRF token from a different account must not authorize a mutation.
        response = second.patch('/api/v1/calculations/demo-calc-001/items/se-ez9f34116', json={
            'expected_revision': 1, 'final_quantity': 36, 'reason': 'Test'},
            headers={'X-CSRF-Token': first_session['csrf_token']})
        assert response.status_code == 403
        assert second.get('/api/v1/auth/me').json()['user']['id'] == second_session['user']['id']


def test_restart_recovers_all_namespaces_without_changing_finished_jobs(app):
    workspaces = ['a' * 32, 'b' * 32, None]
    with app.state.store.transaction() as db:
        for workspace_id in workspaces:
            with workspace_scope(workspace_id):
                for status in ('queued', 'running', 'succeeded', 'failed'):
                    app.state.store.put(db, 'job', status, {'job_id': status, 'status': status, 'owner_marker': workspace_id})
    with TestClient(app):
        with app.state.store.transaction() as db:
            for workspace_id in workspaces:
                with workspace_scope(workspace_id):
                    for original in ('queued', 'running', 'succeeded', 'failed'):
                        job = app.state.store.get(db, 'job', original)
                        assert job['owner_marker'] == workspace_id
                        assert job['status'] == ('failed' if original in ('queued', 'running') else original)
                        if original in ('queued', 'running'):
                            assert job['error']['error']['code'] == 'INTERRUPTED'


def test_stock_state_event_replays_and_policy_updates_are_private(app, monkeypatch):
    from backend.tests.test_stock import initial, event, URL
    from backend.app.stock import _key, _signature

    # A pre-authentication legacy company's event must never satisfy a new user's replay.
    with app.state.store.transaction() as db:
        app.state.store.put(db, 'stock_state', _key('almaty', 'SYNTHETIC-SKU'), {'legacy_private': True})
        app.state.store.put(db, 'stock_event', 'sale-1', {
            'signature': _signature(event()), 'result': {'legacy_private': True}})
    with TestClient(app) as first, TestClient(app) as second:
        assert first.get(URL).status_code == 401
        assert first.post('/api/v1/stock-events', json=event()).status_code == 401
        first_user = register(first, 'stock-first@example.test')['user']['id']
        second_session = register(second, 'stock-second@example.test')
        registered_state = first.post('/api/v1/stock-monitoring', json=initial())
        assert registered_state.status_code == 201, registered_state.text
        assert second.get(URL).status_code == 404
        # Reinitialization cannot overwrite existing state in the same workspace.
        assert first.post('/api/v1/stock-monitoring', json=initial(on_hand=999)).status_code == 409
        assert first.get(URL).json() == registered_state.json()
        assert second.post('/api/v1/stock-events', json=event()).status_code == 404
        # An identical SKU/warehouse and event ID are valid independently per workspace.
        assert second.post('/api/v1/stock-monitoring', json=initial(on_hand=80)).status_code == 201
        first_event = first.post('/api/v1/stock-events', json=event())
        second_event = second.post('/api/v1/stock-events', json=event(on_hand=70))
        assert first_event.status_code == second_event.status_code == 200
        assert first_event.json()['state']['on_hand'] == 39
        assert second_event.json()['state']['on_hand'] == 70
        replay = first.post('/api/v1/stock-events', json=event())
        assert replay.json() == {**first_event.json(), 'deduplicated': True}
        assert first.post('/api/v1/stock-events', json=event(on_hand=38)).status_code == 409
        policy = {**second_event.json()['state']['policy'], 'policy_version': 'private-policy-v2'}
        body = {'expected_revision': 2, 'policy': policy, 'reason': 'Private policy review'}
        assert second.put(URL + '/policy', json=body, headers={'X-CSRF-Token': ''}).status_code == 403
        monkeypatch.setattr('backend.app.stock.now', lambda: '2026-09-23T07:01:00Z')
        assert second.put(URL + '/policy', json=body).status_code == 200
        assert first.get(URL).json() == first_event.json()['state']
        with app.state.store.transaction() as db:
            owners = dict(db.execute('SELECT id,workspace_id FROM auth_users').fetchall())
            with workspace_scope(owners[first_user], first_user):
                assert len(app.state.store.all(db, 'stock_event')) == 1
                assert len(app.state.store.all(db, 'stock_recalculation')) == 1
                assert app.state.store.all(db, 'stock_policy_audit') == []
            with workspace_scope(owners[second_session['user']['id']], second_session['user']['id']):
                assert len(app.state.store.all(db, 'stock_event')) == 1
                assert len(app.state.store.all(db, 'stock_policy_audit')) == 1
        assert first.options(URL + '/policy', headers={'Origin': 'http://localhost:5173',
            'Access-Control-Request-Method': 'PUT', 'Access-Control-Request-Headers': 'X-CSRF-Token'}).status_code == 200
