from contextlib import asynccontextmanager
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from io import StringIO
from pathlib import Path
import csv
import json
import logging
import os
from uuid import uuid4
from dotenv import load_dotenv

from fastapi import BackgroundTasks, FastAPI, File, Form, Header, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

from .contracts import DomainError, ROOT, validate
from .demo import demo_dataset, make_demo, now, summary
from .ingestion import MAX_BYTES, inspect_upload
from .pipeline import calculate
from .storage import Store
from .runtime import DEFAULT_INGESTOR, capabilities, load_adapter
from .ai_review import configuration as ai_configuration, review as review_ai

load_dotenv(ROOT / 'backend' / '.env', override=False)


def error_body(error):
    return {'request_id': str(uuid4()), 'error': {'code': error.code, 'message': error.message, 'details': error.details}}


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _nonnegative_decimal(value, name):
    try:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(name)
        number = Decimal(str(value))
        if not number.is_finite() or number < 0:
            raise ValueError(name)
        return number
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise DomainError('INVALID_PARAMETERS', f'Некорректное значение: {name}.') from exc


def approval_constraints(result, item):
    """Read server-owned decision constraints; never accept them from an override."""
    saved = result.get('approval_constraints')
    if saved is not None:
        value = saved.get(item['item_id']) if isinstance(saved, dict) else None
        required = {'unit_quantum', 'min_order_qty', 'order_multiple',
                    'minimum_safe_quantity', 'constraints_complete'}
        if not isinstance(value, dict) or not required.issubset(value):
            raise DomainError('RECALCULATION_REQUIRED', 'Нет сохранённых ограничений позиции. Повторите расчёт.')
        return value
    if (item.get('forecast') or {}).get('method') != 'contract_example':
        raise DomainError('RECALCULATION_REQUIRED', 'Для проверки изменения нужен новый расчёт с ограничениями модели.')
    # The explicit legacy demo has integer pieces and no model service/material
    # floor. Real/model rows must carry the private sidecar produced by ml_adapter.
    return {'unit_quantum': 1 if item['unit'] == 'шт' else None,
            'min_order_qty': item['min_order_qty'], 'order_multiple': item['order_multiple'],
            'minimum_safe_quantity': 0,
            'constraints_complete': item['min_order_qty'] is not None and item['order_multiple'] is not None}


def validate_quantity(item, quantity, constraints):
    if quantity is None:
        return
    q = _nonnegative_decimal(quantity, 'количество')
    if (constraints['constraints_complete'] is not True
            or constraints['min_order_qty'] is None or constraints['order_multiple'] is None):
        raise DomainError('UNCONFIRMED_CONSTRAINTS', 'Уточните MOQ, кратность и единицы поставки; подтверждение предупреждения не заменяет эти данные.')
    if constraints['unit_quantum'] is None:
        raise DomainError('RECALCULATION_REQUIRED', 'Неизвестна физическая единица округления. Повторите расчёт.')
    quantum = _nonnegative_decimal(constraints['unit_quantum'], 'единица округления')
    minimum = _nonnegative_decimal(constraints['min_order_qty'], 'минимальная партия')
    multiple = _nonnegative_decimal(constraints['order_multiple'], 'кратность')
    floor = _nonnegative_decimal(constraints['minimum_safe_quantity'], 'минимальная допустимая потребность')
    if not quantum or not multiple:
        raise DomainError('RECALCULATION_REQUIRED', 'Единица округления и кратность должны быть положительными.')
    if (item['min_order_qty'] is None or item['order_multiple'] is None
            or _nonnegative_decimal(item['min_order_qty'], 'публичный MOQ') != minimum
            or _nonnegative_decimal(item['order_multiple'], 'публичная кратность') != multiple):
        raise DomainError('RECALCULATION_REQUIRED', 'Ограничения позиции расходятся с сохранённым расчётом.')
    if q % quantum:
        raise DomainError('INVALID_PARAMETERS', f'Количество должно быть кратно физической единице {quantum} {item["unit"]}.')
    if q < floor:
        raise DomainError('MINIMUM_QUANTITY_VIOLATION', 'Количество ниже обязательной потребности или минимума политики. Измените исходные данные и повторите расчёт.')
    if q > 0:
        if q < minimum:
            raise DomainError('MOQ_VIOLATION', f"Минимальная партия: {minimum} {item['unit']}.")
        if q % multiple:
            raise DomainError('MOQ_VIOLATION', f'Количество должно быть кратно {multiple}.')


def order_cost(item, quantity):
    """Compute cost from current quantity and price, never a cached line total."""
    if quantity is None:
        return None
    q = _nonnegative_decimal(quantity, 'количество')
    if q == 0:
        return Decimal(0)
    if item['unit_cost_kzt'] is None:
        return None
    return q * _nonnegative_decimal(item['unit_cost_kzt'], 'закупочная цена')


def create_app(db_path=None):
    data_dir = Path(os.getenv('DATA_DIR', str(ROOT / 'data')))
    store = Store(db_path or data_dir / 'tirek.sqlite3')

    @asynccontextmanager
    async def lifespan(app):
        with store.transaction() as db:
            for job in store.all(db, 'job'):
                if job['status'] in ('queued', 'running'):
                    job.update(status='failed', stage='Сервер был перезапущен', updated_at=now(),
                               error=error_body(DomainError('INTERRUPTED', 'Повторите операцию после перезапуска.')))
                    store.put(db, 'job', job['job_id'], job)
            if not store.all(db, 'dataset'):
                store.put(db, 'dataset', 'demo-systeme-v1', demo_dataset())
            if not store.all(db, 'calculation'):
                store.put(db, 'calculation', 'demo-calc-001', make_demo())
        yield

    app = FastAPI(title='Tirek Platform API', version='1.0.0', lifespan=lifespan)
    app.state.store = store
    app.add_middleware(CORSMiddleware,
                       allow_origins=['http://127.0.0.1:5173', 'http://localhost:5173'],
                       allow_methods=['GET', 'POST', 'PATCH'], allow_headers=['Content-Type', 'Idempotency-Key'])

    @app.exception_handler(DomainError)
    async def domain_error(request, exc):
        return JSONResponse(error_body(exc), status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def request_error(request, exc):
        return JSONResponse(error_body(DomainError('INVALID_PARAMETERS', 'Запрос не соответствует формату API.')), status_code=422)

    @app.exception_handler(Exception)
    async def unexpected_error(request, exc):
        logging.exception('Unhandled API error')
        return JSONResponse(error_body(DomainError('INTERNAL_ERROR', 'Не удалось выполнить операцию. Повторите попытку.', 500)), status_code=500)

    prefix = '/api/v1'

    @app.get(prefix + '/health')
    def health():
        return {'status': 'ok', 'api_version': '1.0.0', 'supported_suppliers': ['systeme-electric', 'iek'],
                'supported_horizons': [28], 'llm_available': ai_configuration()['available'], 'max_file_count': 6, 'max_total_upload_bytes': MAX_BYTES}

    @app.get(prefix + '/workspace')
    def workspace():
        available = capabilities()
        with store.transaction() as db:
            calculations = store.all(db, 'calculation')
            return {'latest_calculation_id': calculations[0]['response']['meta']['calculation_id'] if calculations else None,
                    'datasets': store.all(db, 'dataset'), 'approvals': [a['public'] for a in store.all(db, 'approval')],
                    'calculations': [c['response']['meta'] for c in calculations],
                    'capabilities': available}

    @app.get(prefix + '/datasets/{dataset_id}')
    def dataset(dataset_id: str):
        with store.transaction() as db:
            return store.get(db, 'dataset', dataset_id)

    @app.get(prefix + '/jobs/{job_id}')
    def job(job_id: str):
        with store.transaction() as db:
            return store.get(db, 'job', job_id)

    def finish_job(job_id, worker, resource_type):
        try:
            with store.transaction() as db:
                value = store.get(db, 'job', job_id)
                value.update(status='running', stage='Проверка данных' if resource_type == 'dataset' else 'Подготовка рекомендаций', progress_pct=25, updated_at=now())
                store.put(db, 'job', job_id, value)
            resource_id, resource = worker()
            with store.transaction() as db:
                store.put(db, resource_type, resource_id, resource)
                value.update(status='succeeded', stage='Готово', progress_pct=100, resource_type=resource_type,
                             resource_id=resource_id, updated_at=now())
                store.put(db, 'job', job_id, value)
        except Exception as exc:
            failure = exc if isinstance(exc, DomainError) else DomainError('INTERNAL_ERROR', 'Обработка не завершена. Проверьте формат источников и повторите попытку.', 500)
            if not isinstance(exc, DomainError):
                logging.exception('Job failed')
            with store.transaction() as db:
                value = store.get(db, 'job', job_id)
                value.update(status='failed', stage='Не удалось завершить', error=error_body(failure), updated_at=now())
                store.put(db, 'job', job_id, value)

    def queue(background, scope, key, fingerprint, kind, worker, resource_type):
        if not key.strip():
            raise DomainError('INVALID_PARAMETERS', 'Нужен Idempotency-Key.')
        with store.transaction() as db:
            prior = store.replay(db, scope, key, fingerprint)
            if prior:
                return store.get(db, 'job', prior['job_id'])
            value = {'job_id': str(uuid4()), 'kind': kind, 'status': 'queued', 'stage': 'В очереди', 'progress_pct': 0,
                     'resource_type': None, 'resource_id': None, 'error': None, 'created_at': now(), 'updated_at': now()}
            store.put(db, 'job', value['job_id'], value)
            store.remember(db, scope, key, fingerprint, value)
        background.add_task(finish_job, value['job_id'], worker, resource_type)
        return value

    @app.post(prefix + '/datasets/import', status_code=202)
    async def import_dataset(background: BackgroundTasks, files: list[UploadFile] = File(...),
                             supplier_id: str = Form(...), context: str | None = Form(None),
                             idempotency_key: str = Header(...)):
        if len(files) > 6:
            raise DomainError('INVALID_FILE', 'Можно загрузить не более шести файлов.', 400)
        loaded, total = [], 0
        for file in files:
            content = await file.read(MAX_BYTES + 1)
            total += len(content)
            await file.close()
            if total > MAX_BYTES:
                raise DomainError('UPLOAD_TOO_LARGE', 'Общий размер превышает 30 МБ.', 413)
            loaded.append((file.filename or 'unknown', content))
        try:
            extra = json.loads(context) if context else None
        except ValueError as exc:
            raise DomainError('INVALID_PARAMETERS', 'Дополнительный контекст должен быть корректным JSON.') from exc
        fingerprint = digest({'supplier_id': supplier_id, 'files': [(name, sha256(content).hexdigest()) for name, content in loaded], 'context': extra})

        def worker():
            report = inspect_upload(loaded, supplier_id, extra)
            target = data_dir / 'uploads' / report['dataset_id']
            target.mkdir(parents=True, exist_ok=True)
            for source, (_, content) in zip(report['sources'], loaded):
                (target / (source['role'] + '.xlsx')).write_bytes(content)
            (target / 'manifest.json').write_text(json.dumps({'report': report, 'context': extra}, ensure_ascii=False, indent=2), encoding='utf-8')
            normalizer = os.getenv('TIREK_INGESTOR', DEFAULT_INGESTOR)
            normalized = load_adapter(normalizer)(target, deepcopy(report), extra)
            validate('DatasetReport', normalized)
            if normalized['dataset_id'] != report['dataset_id']:
                raise DomainError('INVALID_PARAMETERS', 'Нормализатор изменил ID исходного набора.')
            report = normalized
            (target / 'manifest.json').write_text(json.dumps({'report': report, 'context': extra}, ensure_ascii=False, indent=2), encoding='utf-8')
            return report['dataset_id'], report

        return queue(background, 'import', idempotency_key, fingerprint, 'import', worker, 'dataset')

    @app.post(prefix + '/calculations', status_code=202)
    def start_calculation(payload: dict, background: BackgroundTasks, idempotency_key: str = Header(...)):
        validate('CalculationRequest', payload)
        if payload['horizon_days'] != 28:
            raise DomainError('UNSUPPORTED_HORIZON', 'Сейчас доступен горизонт 28 дней.')
        if payload['lead_time_days'] + payload['review_period_days'] != payload['horizon_days']:
            raise DomainError('INVALID_PARAMETERS', 'Горизонт должен равняться сроку поставки плюс период пересмотра.')
        with store.transaction() as db:
            source = store.get(db, 'dataset', payload['dataset_id'])
        if not source['calculation_allowed']:
            raise DomainError('MISSING_CRITICAL_DATA', 'Набор ещё не нормализован. Сначала подключите адаптер данных и расчётный модуль.')
        synthetic_context = any(issue['code'] == 'SYNTHETIC_CONTEXT_BLOCKS_OPERATIONAL' for issue in source['issues'])
        if payload['mode'] == 'operational' and (source['source_kind'] != 'observed' or synthetic_context or any(
                entry.get('source_kind') == 'synthetic' for key in ('category_policies', 'economic_profiles', 'growth_adjustments') for entry in payload[key])):
            raise DomainError('INVALID_PARAMETERS', 'Операционный режим не допускает синтетические данные и параметры.')
        if payload['as_of_date'] < source['data_as_of']:
            raise DomainError('INVALID_PARAMETERS', 'Дата расчёта не может предшествовать дате данных.')
        calculation_id = str(uuid4())
        return queue(background, 'calculate', idempotency_key, digest(payload), 'calculation',
                     lambda: (calculation_id, calculate(source, payload, calculation_id)), 'calculation')

    @app.get(prefix + '/calculations/{calculation_id}/recommendations')
    def recommendations(calculation_id: str):
        with store.transaction() as db:
            return store.get(db, 'calculation', calculation_id)['response']

    @app.get(prefix + '/calculations/{calculation_id}/items/{item_id}')
    def item_detail(calculation_id: str, item_id: str):
        with store.transaction() as db:
            result = store.get(db, 'calculation', calculation_id)
            if item_id not in result['details']:
                raise DomainError('NOT_FOUND', 'Товар не найден.', 404)
            return result['details'][item_id]

    @app.post(prefix + '/calculations/{calculation_id}/items/{item_id}/ai-review')
    def ai_review(calculation_id: str, item_id: str, payload: dict):
        revision = payload.get('expected_revision')
        if set(payload) != {'expected_revision'} or type(revision) is not int or revision < 1:
            raise DomainError('INVALID_PARAMETERS', 'Укажите текущую ревизию расчёта.')
        with store.transaction() as db:
            result = store.get(db, 'calculation', calculation_id)
            if result['response']['meta']['revision'] != revision:
                raise DomainError('STALE_REVISION', 'Расчёт изменился. Обновите карточку и повторите проверку.', 409)
            if item_id not in result['details']:
                raise DomainError('NOT_FOUND', 'Товар не найден.', 404)
            detail = deepcopy(result['details'][item_id])
        judgement = review_ai(detail)
        validate('AIJudgement', judgement)
        with store.transaction() as db:
            current = store.get(db, 'calculation', calculation_id)
            if current['response']['meta']['revision'] != revision:
                raise DomainError('STALE_REVISION', 'Расчёт изменился во время проверки ИИ. Ответ не применён.', 409)
            current['details'][item_id]['item']['ai'] = judgement
            for item in current['response']['items']:
                if item['item_id'] == item_id:
                    item['ai'] = deepcopy(judgement)
            store.put(db, 'calculation', calculation_id, current)
            return current['details'][item_id]

    @app.patch(prefix + '/calculations/{calculation_id}/items/{item_id}')
    def override(calculation_id: str, item_id: str, payload: dict):
        validate('OverrideRequest', payload)
        if not payload['reason'].strip():
            raise DomainError('INVALID_PARAMETERS', 'Укажите причину изменения.')
        with store.transaction() as db:
            result = store.get(db, 'calculation', calculation_id)
            response = result['response']
            if response['meta']['revision'] != payload['expected_revision']:
                raise DomainError('STALE_REVISION', 'Рекомендации изменились. Данные обновлены; проверьте количество и повторите сохранение.', 409)
            if item_id not in result['details']:
                raise DomainError('NOT_FOUND', 'Товар не найден.', 404)
            item = result['details'][item_id]['item']
            quantity = payload['final_quantity']
            if item['decision_status'] == 'needs_data' and quantity is not None:
                raise DomainError('MISSING_CRITICAL_DATA', 'Сначала добавьте недостающие исходные данные.')
            if quantity is not None:
                validate_quantity(item, quantity, approval_constraints(result, item))
            cost = order_cost(item, quantity)
            item.update(final_quantity=quantity, override_reason=payload['reason'].strip(),
                        order_cost_kzt=None if cost is None else float(cost))
            response['meta']['revision'] += 1
            for detail in result['details'].values():
                detail['meta'] = response['meta']
                if detail['item']['ai']['status'] == 'reviewed':
                    # The LLM context contains calculation_revision, so every
                    # review belongs to the previous revision, even on other rows.
                    detail['item']['ai'] = {
                        'status': 'unavailable', 'verdict': None,
                        'reasons': ['AI-01: расчёт изменён; заключение предыдущей ревизии больше не актуально.'],
                        'evidence_ids': [], 'rule_ids': ['AI-01'],
                        'suggested_action': None, 'provider_model': None,
                    }
            response['items'] = [result['details'][i['item_id']]['item'] for i in response['items']]
            response['summary'] = summary(response['items'])
            store.put(db, 'calculation', calculation_id, result)
            return result['details'][item_id]

    @app.post(prefix + '/calculations/{calculation_id}/approve', status_code=201)
    def approve(calculation_id: str, payload: dict, idempotency_key: str = Header(...)):
        validate('ApprovalRequest', payload)
        if not idempotency_key.strip():
            raise DomainError('INVALID_PARAMETERS', 'Нужен Idempotency-Key.')
        scope = 'approve:' + calculation_id
        fingerprint = digest(payload)
        with store.transaction() as db:
            prior = store.replay(db, scope, idempotency_key, fingerprint)
            if prior:
                return prior
            result = store.get(db, 'calculation', calculation_id)
            meta = result['response']['meta']
            if payload['expected_revision'] != meta['revision']:
                raise DomainError('STALE_REVISION', 'Рекомендации изменились. Обновите выбор перед утверждением.', 409)
            selected = payload['selected_item_ids']
            if not selected or len(selected) != len(set(selected)):
                raise DomainError('INVALID_PARAMETERS', 'Выберите хотя бы одну позицию без повторов.')
            items, total_cost, prices_complete = [], Decimal(0), True
            for item_id in selected:
                if item_id not in result['details']:
                    raise DomainError('NOT_FOUND', 'Выбранный товар не найден.', 404)
                item = result['details'][item_id]['item']
                if item['decision_status'] == 'needs_data' or item['final_quantity'] is None:
                    raise DomainError('MISSING_CRITICAL_DATA', f"Нельзя утвердить {item['sku']}: недостаточно данных.")
                validate_quantity(item, item['final_quantity'], approval_constraints(result, item))
                if item['decision_status'] == 'needs_review':
                    if any(issue['code'] not in payload['acknowledged_issue_codes'] for issue in item['issues']):
                        raise DomainError('REVIEW_REQUIRED', f"Подтвердите предупреждения по {item['sku']}.")
                cost = order_cost(item, item['final_quantity'])
                prices_complete = prices_complete and cost is not None
                if cost is not None:
                    total_cost += cost
                selected_item = deepcopy(item)
                selected_item['order_cost_kzt'] = None if cost is None else float(cost)
                items.append(selected_item)
            budget = result['request'].get('budget_kzt')
            if budget is not None:
                if not prices_complete:
                    raise DomainError('PRICE_REQUIRED_FOR_BUDGET', 'Для проверки бюджета нужны закупочные цены всех выбранных позиций.')
                if total_cost > _nonnegative_decimal(budget, 'бюджет'):
                    raise DomainError('BUDGET_EXCEEDED', 'Сумма выбранных позиций превышает бюджет. Измените состав заказа.')
            approval_id = str(uuid4())
            public = {'approval_id': approval_id, 'calculation_id': calculation_id, 'revision': meta['revision'],
                      'mode': meta['mode'], 'status': 'approved', 'approved_at': now(), 'selected_item_ids': selected,
                      'export_url': f'{prefix}/approvals/{approval_id}/export.csv'}
            store.put(db, 'approval', approval_id, {'public': public, 'meta': deepcopy(meta), 'items': deepcopy(items)})
            store.remember(db, scope, idempotency_key, fingerprint, public)
            return public

    @app.get(prefix + '/approvals/{approval_id}/export.csv')
    def export(approval_id: str):
        with store.transaction() as db:
            snapshot = store.get(db, 'approval', approval_id)
        stream = StringIO(newline='')
        fields = ['approval_id', 'calculation_id', 'revision', 'mode', 'data_as_of', 'supplier_id', 'sku', 'name', 'unit',
                  'recommended_quantity', 'final_quantity', 'override_reason', 'urgency', 'reason', 'model_id', 'policy_version', 'economics_source']
        writer = csv.DictWriter(stream, fieldnames=fields, delimiter=';', extrasaction='ignore')
        writer.writeheader()
        for item in snapshot['items']:
            row = {**snapshot['meta'], **item, 'approval_id': approval_id,
                   'model_id': item['forecast']['model_id'] if item['forecast'] else ''}
            row = {k: ("'" + v if isinstance(v, str) and v.lstrip().startswith(('=', '+', '-', '@', '\t', '\r', '\n')) else v) for k, v in row.items()}
            writer.writerow(row)
        return Response(stream.getvalue().encode('utf-8-sig'), media_type='text/csv; charset=utf-8',
                        headers={'Content-Disposition': f'attachment; filename="tirek-{approval_id[:8]}.csv"'})

    return app


app = create_app()
