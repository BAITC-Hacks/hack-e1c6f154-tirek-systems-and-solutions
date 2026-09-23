"""Integration point owned by the ML developer. Never silently fall back to demo."""
import importlib
import os
from typing import Protocol
from .contracts import DomainError, validate
from .demo import make_demo


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
        target = os.getenv('TIREK_PIPELINE')
        if not target:
            raise DomainError('PIPELINE_NOT_CONFIGURED', 'Источники сохранены. Подключите модуль нормализации и прогноза для расчёта на реальных данных.')
        module, name = target.split(':', 1)
        result = getattr(importlib.import_module(module), name)().calculate(dataset, request, calculation_id)
    validate('RecommendationsResponse', result['response'])
    for detail in result['details'].values():
        validate('ItemDetail', detail)
    result['request'] = request
    return result
