"""Integration point owned by the ML developer. Never silently fall back to demo."""
import os
from copy import deepcopy
from datetime import date, timedelta
import math
from typing import Protocol
from model.decision.rules import build_ai_context, validate_ai_judgement
from .contracts import DomainError, validate
from .demo import make_demo, summary
from .runtime import DEFAULT_PIPELINE, load_adapter
from .ai_review import review_calculation


def _invalid(message):
    raise DomainError('INVALID_PIPELINE_RESULT', message, 500)


def _finite_tree(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            finite = math.isfinite(value)
        except OverflowError:
            finite = False
        if not finite:
            _invalid('Расчётный модуль вернул неограниченное числовое значение.')
    elif isinstance(value, dict):
        for child in value.values():
            _finite_tree(child)
    elif isinstance(value, list):
        for child in value:
            _finite_tree(child)


def _unavailable_ai(message):
    return {'status': 'unavailable', 'verdict': None, 'reasons': ['AI-01: ' + message],
            'evidence_ids': [], 'rule_ids': ['AI-01'], 'suggested_action': None, 'provider_model': None}


def _checked_ai(item, detail, meta, request, provenance):
    """Optional review can fail without discarding the deterministic calculation.

    A real provider adapter may return a private ``ai_provenance[item_id]`` record
    with call_id, provider_called=True, provider_model and the exact request_context
    built by build_ai_context. This is server-owned call evidence, never HTTP input.
    Missing records cannot create a reviewed verdict. No provider is called here.
    """
    raw = item.get('ai')
    try:
        validate('AIJudgement', raw)
        context = build_ai_context(
            evidence=item['evidence'],
            versions={'dataset_version': meta['dataset_version'], 'policy_version': meta['policy_version'],
                      'calculation_revision': meta['revision']},
            allowed_rule_ids=detail['applied_rule_ids'], recommended_quantity=item['recommended_quantity'],
            product_text=item['name'])
        if raw['status'] != 'reviewed':
            if (raw['verdict'] is not None or raw['suggested_action'] is not None or raw['provider_model'] is not None
                    or not set(raw['evidence_ids']) <= {fact['id'] for fact in item['evidence']}
                    or not set(raw['rule_ids']) <= set(detail['applied_rule_ids']) | {'AI-01'}):
                return _unavailable_ai('некорректный статус или ссылки заключения; расчёт сохранён.')
            return deepcopy(raw)
        if (not request['request_ai_review'] or not isinstance(provenance, dict)
                or not isinstance(provenance.get('call_id'), str) or not provenance['call_id'].strip()):
            return _unavailable_ai('нет подтверждённого вызова провайдера для этой ревизии; расчёт сохранён.')
        return validate_ai_judgement(
            raw, request_context=provenance.get('request_context'), current_context=context,
            provider_called=provenance.get('provider_called') is True,
            provider_model=provenance.get('provider_model'))
    except (DomainError, ValueError, TypeError, KeyError, OverflowError):
        return _unavailable_ai('ответ ИИ или его факты не прошли проверку; расчёт сохранён.')


def _semantic_result(result, dataset, request, calculation_id):
    """Validate point-in-time metadata, horizon semantics and optional AI review."""
    response = result['response']
    meta = response.get('meta')
    validate('CalculationMetadata', meta)
    expected = {'calculation_id': calculation_id, 'dataset_id': dataset['dataset_id'],
                'dataset_version': dataset['dataset_version'], 'data_as_of': dataset['data_as_of'],
                'as_of_date': request['as_of_date'], 'mode': request['mode'],
                'horizon_days': request['horizon_days'], 'revision': 1}
    if any(meta[key] != value for key, value in expected.items()):
        _invalid('Метаданные результата расходятся с исходным набором или параметрами расчёта.')
    items = response.get('items')
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        _invalid('Расчётный модуль вернул некорректный список позиций.')
    start = date.fromisoformat(request['as_of_date']) + timedelta(days=1)
    end = start + timedelta(days=request['horizon_days'] - 1)
    for item in items:
        detail = result['details'].get(item.get('item_id'))
        if not isinstance(detail, dict) or not isinstance(detail.get('item'), dict):
            _invalid('Для позиции отсутствует детализация.')
        # Check equality before normalization, so sanitizing AI cannot mask a
        # disagreement between the public list and its detail response.
        if detail['item'] != item or detail.get('meta') != meta:
            _invalid('Публичная строка расходится с детализацией.')
        forecast = item.get('forecast')
        if forecast is not None:
            validate('Forecast', forecast)
            if (forecast['horizon_days'] != request['horizon_days']
                    or forecast['period_start'] != start.isoformat() or forecast['period_end'] != end.isoformat()):
                _invalid('Даты или горизонт прогноза расходятся с расчётом.')
            quantiles = [forecast[key] for key in ('p10', 'p50', 'p90') if forecast[key] is not None]
            if quantiles != sorted(quantiles):
                _invalid('Квантили прогноза должны возрастать от p10 к p90.')
        if item.get('decision_status') == 'needs_data':
            if item.get('final_quantity') is not None:
                _invalid('Позиция без критичных данных не может иметь итоговое количество.')
            if item.get('recommended_quantity') is not None:
                issues = item.get('issues', [])
                if (not any(issue.get('code') == 'PROVISIONAL_QUANTITY' for issue in issues)
                        or not any(issue.get('severity') == 'error' for issue in issues)):
                    _invalid('Предварительное количество без критичных данных должно быть явно помечено и заблокировано.')
        if item.get('decision_status') == 'no_order' and item.get('recommended_quantity') != 0:
            _invalid('Статус no_order требует нулевой рекомендации.')
        provenance = result.get('ai_provenance', {})
        checked = _checked_ai(item, detail, meta, request,
                              provenance.get(item.get('item_id')) if isinstance(provenance, dict) else None)
        item['ai'] = checked
        detail['item']['ai'] = deepcopy(checked)
    # Provider responses can contain NaN which JSON Schema's numeric minimum
    # alone does not reliably reject. AI has already failed closed separately.
    _finite_tree(response)
    _finite_tree(result['details'])


class CalculationPipeline(Protocol):
    def calculate(self, dataset: dict, request: dict, calculation_id: str) -> dict:
        """Return {response: RecommendationsResponse, details: {item_id: ItemDetail}, request}.

        Normalized data/raw manifest can be loaded using dataset_id from DATA_DIR.
        Implement forecasting and replenishment here, outside HTTP handlers.
        """
        ...


def calculate(dataset, request, calculation_id):
    if dataset['source_kind'] == 'synthetic':
        if request['mode'] != 'scenario':
            raise DomainError('INVALID_PARAMETERS', 'Синтетический набор доступен только в сценарном режиме.')
        if request['economic_profiles'] or request['growth_adjustments'] or request['category_policies']:
            raise DomainError('INVALID_PARAMETERS', 'Демо использует готовые профили. Пользовательские политики обрабатывает будущий расчётный модуль.')
        if (request['lead_time_days'], request['review_period_days']) != (7, 21):
            raise DomainError('INVALID_PARAMETERS', 'Демонстрационный сценарий подготовлен для срока 7 дней и пересмотра 21 день.')
        result = make_demo(calculation_id, request)
    else:
        target = os.getenv('TIREK_PIPELINE', DEFAULT_PIPELINE)
        result = load_adapter(target)().calculate(dataset, request, calculation_id)
    if not isinstance(result, dict) or not isinstance(result.get('response'), dict) or not isinstance(result.get('details'), dict):
        raise DomainError('INVALID_PIPELINE_RESULT', 'Расчётный модуль вернул неполный результат.', 500)
    result = deepcopy(result)
    _semantic_result(result, dataset, request, calculation_id)
    validate('RecommendationsResponse', result['response'])
    items = result['response']['items']
    item_ids = [item['item_id'] for item in items]
    if len(item_ids) != len(set(item_ids)) or set(result['details']) != set(item_ids):
        raise DomainError('INVALID_PIPELINE_RESULT', 'Строки рекомендации и детализации не согласованы.', 500)
    if result['response']['summary'] != summary(items):
        raise DomainError('INVALID_PIPELINE_RESULT', 'Итоги расчёта не совпадают со строками.', 500)
    for item in items:
        detail = result['details'][item['item_id']]
        validate('ItemDetail', detail)
        if detail['item'] != item or detail['meta'] != result['response']['meta']:
            raise DomainError('INVALID_PIPELINE_RESULT', 'Публичная строка расходится с детализацией.', 500)
    meta = result['response']['meta']
    if meta['calculation_id'] != calculation_id or meta['dataset_id'] != dataset['dataset_id']:
        raise DomainError('INVALID_PIPELINE_RESULT', 'Расчётный модуль вернул неверную идентичность результата.', 500)
    if request['request_ai_review']:
        review_calculation(result)
    result['request'] = request
    return result
