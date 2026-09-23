"""Persistent stock signals; explicit snapshots, no supplier sending or ML calls.

Initialization is an explicit snapshot import. Existing calculation recommendations
are never silently converted into observed inventory. Recalculation intents are
persisted as pending records; a worker is not connected to this queue yet.
"""
from copy import deepcopy
from hashlib import sha256
import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from jsonschema import Draft202012Validator, FormatChecker
import yaml

from model.decision.rules import EventConflict, change_stock_policy, create_stock_state, reduce_stock_event
from .contracts import DomainError, ROOT
from .demo import now


SPEC = yaml.safe_load((ROOT / 'contracts/stock-monitoring.openapi.yaml').read_text(encoding='utf-8'))


def validate_stock(schema, value):
    """Validate strict stock JSON including finite numbers and timezone-aware dates."""
    try:
        json.dumps(value, allow_nan=False)
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError('Stock payload must contain finite JSON values.') from exc
    validator = Draft202012Validator(
        {'$ref': '#/components/schemas/' + schema, 'components': SPEC['components']},
        format_checker=FormatChecker())
    if next(validator.iter_errors(value), None) is not None:
        raise ValueError('Запрос не соответствует формату складского API.')


def _key(warehouse_id, sku):
    if not all(isinstance(value, str) and value.strip() for value in (warehouse_id, sku)):
        raise ValueError('Нужны склад и артикул.')
    return sha256(json.dumps([warehouse_id, sku], ensure_ascii=False).encode()).hexdigest()


def _signature(payload):
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(',', ':'))


def _error(exc):
    if isinstance(exc, DomainError):
        status, code, message = exc.status, exc.code, exc.message
    elif isinstance(exc, EventConflict):
        status, code, message = 409, 'STOCK_CONFLICT', str(exc)
    else:
        status, code, message = 422, 'INVALID_PARAMETERS', str(exc)
    return JSONResponse({'code': code, 'message': message}, status_code=status)


async def _payload(request, schema):
    try:
        payload = await request.json()
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError('Ожидается JSON складского события.') from exc
    validate_stock(schema, payload)
    return payload


def _persist_result(store, db, result):
    state, transition = result['state'], result['transition']
    validate_stock('EventResult', result)
    store.put(db, 'stock_state', _key(state['warehouse_id'], state['sku']), state)
    if transition is not None:
        store.put(db, 'stock_transition', transition['transition_id'], {
            **transition, 'sku': state['sku'], 'warehouse_id': state['warehouse_id'],
            'revision': state['revision'], 'mode': state['mode']})
    if result['recalculation_requested']:
        # Durable intent, never a claim that forecasting or approval has run.
        store.put(db, 'stock_recalculation', transition['transition_id'], {
            'request_id': transition['transition_id'], 'status': 'pending',
            'sku': state['sku'], 'warehouse_id': state['warehouse_id'],
            'stock_revision': state['revision'], 'policy_version': state['policy']['policy_version'],
            'mode': state['mode'], 'created_at': state['evaluated_at'], 'calculation_id': None})


def register_stock_routes(app, store):
    """Attach stock routes backed by the same transactional SQLite store as the API.

    Event IDs are globally unique and replay original results. Replays never
    overwrite current state or duplicate transitions/queue records. PUT policies
    use server time; both timestamps and revisions must move monotonically.
    """
    router = APIRouter(prefix='/api/v1', tags=['stock-monitoring'])

    @router.post('/stock-monitoring', status_code=201)
    async def initialize(request: Request):
        try:
            payload = await _payload(request, 'InitialState')
            state = create_stock_state(**payload)
            validate_stock('State', state)
            object_id = _key(state['warehouse_id'], state['sku'])
            with store.transaction() as db:
                if db.execute('SELECT 1 FROM objects WHERE kind=? AND id=?', ('stock_state', object_id)).fetchone():
                    raise EventConflict('State already exists; apply a stock event or update its policy.')
                store.put(db, 'stock_state', object_id, state)
            return state
        except (DomainError, ValueError, TypeError) as exc:
            return _error(exc)

    @router.get('/stock-monitoring/{warehouse_id}/{sku}')
    async def state(warehouse_id: str, sku: str):
        try:
            with store.transaction() as db:
                result = store.get(db, 'stock_state', _key(warehouse_id, sku))
            validate_stock('State', result)
            return result
        except (DomainError, ValueError, TypeError) as exc:
            return _error(exc)

    @router.post('/stock-events')
    async def apply_event(request: Request):
        try:
            event = await _payload(request, 'StockEvent')
            with store.transaction() as db:
                prior = db.execute('SELECT payload FROM objects WHERE kind=? AND id=?',
                                   ('stock_event', event['event_id'])).fetchone()
                if prior:
                    saved = json.loads(prior[0])
                    if saved['signature'] != _signature(event):
                        raise EventConflict('event_id reused with different content')
                    result = deepcopy(saved['result'])
                    result['deduplicated'] = True
                    return result
                old = store.get(db, 'stock_state', _key(event['warehouse_id'], event['sku']))
                _, _, result = reduce_stock_event(old, event)
                _persist_result(store, db, result)
                store.put(db, 'stock_event', event['event_id'], {
                    'signature': _signature(event), 'event': event, 'result': result})
            return result
        except (DomainError, ValueError, TypeError) as exc:
            return _error(exc)

    @router.put('/stock-monitoring/{warehouse_id}/{sku}/policy')
    async def update_policy(warehouse_id: str, sku: str, request: Request):
        try:
            payload = await _payload(request, 'PolicyUpdate')
            with store.transaction() as db:
                old = store.get(db, 'stock_state', _key(warehouse_id, sku))
                result = change_stock_policy(old, **payload, evaluated_at=now())
                audit = result.pop('policy_audit')
                _persist_result(store, db, result)
                store.put(db, 'stock_policy_audit', result['transition']['transition_id'], {
                    **audit, 'sku': sku, 'warehouse_id': warehouse_id, 'revision': result['state']['revision'],
                    'evaluated_at': result['state']['evaluated_at']})
            return result
        except (DomainError, ValueError, TypeError) as exc:
            return _error(exc)

    app.include_router(router)
