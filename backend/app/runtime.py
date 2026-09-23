"""Resolve optional adapters and report their actual availability."""
import importlib
import json
import os
from hashlib import sha256

from .contracts import DomainError, ROOT

DEFAULT_INGESTOR = 'backend.app.ml_adapter:normalize_dataset'
DEFAULT_PIPELINE = 'backend.app.ml_adapter:TirekCalculationPipeline'


def load_adapter(target):
    try:
        module, name = target.split(':', 1)
        adapter = getattr(importlib.import_module(module), name)
        if not callable(adapter):
            raise TypeError('Adapter must be callable')
        return adapter
    except (ImportError, AttributeError, ValueError, TypeError) as exc:
        raise DomainError('ADAPTER_UNAVAILABLE',
                          'Расчётный адаптер недоступен. Установите backend/requirements-ml.txt '
                          'в Python 3.12 и проверьте настройки TIREK_INGESTOR/TIREK_PIPELINE.', 503) from exc


def capabilities():
    pipeline = os.getenv('TIREK_PIPELINE', DEFAULT_PIPELINE)
    normalizer = os.getenv('TIREK_INGESTOR', DEFAULT_INGESTOR)
    result = {'demo': True, 'ml_connected': False, 'real_import': 'unavailable', 'ml_error': None}
    try:
        load_adapter(normalizer)
        result['real_import'] = 'normalized'
        load_adapter(pipeline)
        if pipeline == DEFAULT_PIPELINE:
            directory = ROOT / 'model' / 'forecast_v2' / 'artifacts'
            manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
            selection_bytes = (directory / 'selection.json').read_bytes()
            lock = json.loads((directory / 'selection.lock.json').read_text(encoding='utf-8'))
            if sha256(selection_bytes).hexdigest() != lock['selection_sha256'] or manifest['selection_sha256'] != lock['selection_sha256']:
                raise ValueError('Frozen selection hash mismatch')
            from model.forecast_v2.run import verify_prediction_implementation
            verify_prediction_implementation(json.loads(selection_bytes))
            required = ['selection.json', 'selection.lock.json'] + [
                model['file'] for panel in manifest['panels'].values() for model in panel['models'].values()
            ]
            if any(not (directory / filename).is_file() for filename in required):
                raise ValueError('Missing forecast artifacts')
        result['ml_connected'] = True
    except DomainError as exc:
        result['ml_error'] = exc.message
    except (OSError, ValueError, KeyError, TypeError):
        result['ml_error'] = 'Не найдены совместимые артефакты прогноза. Проверьте model/forecast_v2/artifacts и формат LF в .gitattributes.'
    return result
