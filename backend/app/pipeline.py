"""Integration point owned by the ML developer. Never silently fall back to demo."""
import os
from typing import Protocol
from .contracts import DomainError, validate
from .demo import make_demo, summary
from .runtime import DEFAULT_PIPELINE, load_adapter
from .ai_review import review_calculation


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
