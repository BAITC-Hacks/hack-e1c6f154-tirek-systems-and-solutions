"""Observed IEK sales -> frozen forecast. Missing current stock is never invented."""
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil

import numpy as np
import pandas as pd
from openpyxl import load_workbook

from model.forecast_v2.data import code, load_panels
from .contracts import DomainError, ROOT
from .demo import now, summary
from .ml_adapter import _history, _issue, _load_forecast, _write_json
from .demand_context import context_issues, normalize_events, regular_forecasts, validate_intervals
from .inventory_template import read_current_stock


def normalize_dataset(directory, report, context):
    directory = Path(directory)
    by_role = {source['role']: source for source in report['sources']}
    required = {'sales_transactions', 'sales_monthly'}
    if not required.issubset(by_role):
        report['issues'].append(_issue('NORMALIZATION_BLOCKED', 'error',
                                      'Для прогноза IEK нужны динамика и помесячные продажи.'))
        return report
    folder = directory / 'model_inputs' / 'IEK'
    folder.mkdir(parents=True, exist_ok=True)
    for role, source in by_role.items():
        filename = Path(source['filename']).name
        path = directory / f'{role}.xlsx'
        if sha256(path.read_bytes()).hexdigest() != source['sha256']:
            raise DomainError('INVALID_FILE', 'Контрольная сумма исходного файла изменилась.')
        shutil.copyfile(path, folder / filename)
    try:
        panels, audits = load_panels(folder.parent, ('IEK',))
        if any(panel.warehouse not in ('Алматы', 'almaty') for panel in panels):
            raise ValueError('IEK currently supports sales warehouse Алматы / almaty only')
        events = normalize_events(directory / 'sales_transactions.xlsx', context)
        validate_intervals(context, panels)
        profiles = (read_current_stock(directory / 'current_stock_inbound.xlsx')
                    if 'current_stock_inbound' in by_role else None) or {}
        units = {str(sku): panel.unit for panel in panels for sku in panel.daily.index}
        for sku, profile in profiles.items():
            if sku in units and profile['unit'] != units[sku]:
                raise ValueError('Current stock unit differs from sales for ' + sku)
    except (ValueError, KeyError, OSError) as exc:
        raise DomainError('INVALID_FILE', f'Нормализация IEK не завершена: {exc}', 422) from exc
    names = {}
    workbook = load_workbook(folder / by_role['sales_transactions']['filename'], read_only=True, data_only=True)
    try:
        for row in workbook.worksheets[0].iter_rows(min_row=2, values_only=True):
            if len(row) >= 8 and row[3] is not None:
                names[code(row[3])] = str(row[4] or row[3])
    finally:
        workbook.close()
    normalized = directory / 'normalized'
    normalized.mkdir(exist_ok=True)
    _write_json(normalized / 'names.json', names)
    _write_json(normalized / 'audit.json', audits['IEK'])
    _write_json(normalized / 'context.json', context or {})
    _write_json(normalized / 'events.json', events)
    _write_json(normalized / 'current.json', profiles)
    audit = audits['IEK']
    sales_as_of = audit['transactions']['data_as_of']
    as_of = max([sales_as_of, *[profile['inventory_as_of'] for profile in profiles.values()]])
    for source in report['sources']:
        if source['role'] == 'sales_transactions':
            source.update(rows_used=audit['transactions']['used_positive_invoice_rows'], data_as_of=sales_as_of)
        elif source['role'] == 'sales_monthly':
            source['rows_used'] = audit['monthly']['sku_count']
        elif source['role'] == 'current_stock_inbound' and profiles:
            source.update(rows_used=len(profiles), data_as_of=max(profile['inventory_as_of'] for profile in profiles.values()))
    report.update(data_as_of=as_of, sku_count=sum(len(panel.daily) for panel in panels), calculation_allowed=True)
    if all('SYNTHETIC' in source['filename'] for source in report['sources']):
        report['source_kind'] = 'synthetic'
    report['issues'] = [issue for issue in report['issues']
                        if issue['code'] not in {'NORMALIZATION_REQUIRED', 'SOURCE_DATE_UNKNOWN', 'MISSING_SOURCE'}]
    report['issues'].extend([
        _issue('CURRENT_STOCK_TEMPLATE_APPLIED', 'info', f'Явный складской снимок IEK: {len(profiles)} SKU. Остаток, MOQ, кратность, цена и датированный приход участвуют в расчёте заказа.')
        if profiles else
        _issue('FORECAST_ONLY', 'warning', 'Прогноз IEK рассчитан по наблюдаемым продажам. Для закупки загрузите явный current_stock_inbound по шаблону; прежний «Путь» не содержит актуальный свободный остаток.'),
        _issue('AUXILIARY_SOURCES_NOT_APPLIED', 'warning', 'Если переданы отдельные месячные остатки, сезонность или MOQ, они сохранены для аудита. Условия заказа берутся только из явного актуального снимка; месячные остатки не заменяют его.'),
        *[_issue(code, 'warning', message, reference='normalized/context.json')
          for code, message in context_issues(context)],
        _issue('LAST_DAY_UNVERIFIED', 'warning', 'Полнота последнего дня выгрузки не подтверждена. Он используется как доступная часть дня.'),
    ])
    if context:
        if not profiles and (context.get('price_observations') or context.get('material_requirements')):
            report['issues'].append(_issue('ORDER_CONTEXT_NOT_APPLIED', 'warning', 'Цены и материальные ведомости сохранены; для заказа IEK сначала нужен актуальный складской снимок.'))
        if any(row.get('source_kind') == 'synthetic' for key in ('stockout_intervals', 'price_observations', 'material_requirements') for row in context.get(key, [])):
            report['issues'].append(_issue('SYNTHETIC_CONTEXT_BLOCKS_OPERATIONAL', 'warning', 'Синтетический контекст допускает только сценарный режим.'))
    return report


class IEKForecastPipeline:
    def calculate(self, dataset, request, calculation_id):
        if set(request['warehouse_ids']) - {'almaty'}:
            raise DomainError('INVALID_PARAMETERS', 'В выгрузке IEK поддержан склад Алматы.')
        from .storage import scoped_data_directory
        directory = scoped_data_directory(Path(os.getenv('DATA_DIR', str(ROOT / 'data')))) / 'uploads' / dataset['dataset_id']
        names = json.loads((directory / 'normalized' / 'names.json').read_text(encoding='utf-8'))
        context_path = directory / 'normalized' / 'context.json'
        events_path = directory / 'normalized' / 'events.json'
        context = json.loads(context_path.read_text(encoding='utf-8')) if context_path.exists() else {}
        events = json.loads(events_path.read_text(encoding='utf-8')) if events_path.exists() else []
        panels, _ = load_panels(directory / 'model_inputs', ('IEK',))
        profiles_path = directory / 'normalized' / 'current.json'
        profiles = json.loads(profiles_path.read_text(encoding='utf-8')) if profiles_path.exists() else {}
        if profiles:
            from .ml_adapter import TirekCalculationPipeline
            pipeline = TirekCalculationPipeline()
            result = None
            for panel in panels:
                partial = pipeline.calculate_panel(dataset, request, calculation_id, panel, profiles, context, events)
                if result is None:
                    result = partial
                else:
                    result['details'].update(partial['details'])
                    result['approval_constraints'].update(partial['approval_constraints'])
            for detail in result['details'].values():
                detail['meta'] = result['response']['meta']
            result['response']['items'] = [detail['item'] for detail in result['details'].values()]
            result['response']['summary'] = summary(result['response']['items'])
            return result
        if request['category_codes'] or any(request[key] for key in ('category_policies', 'economic_profiles', 'growth_adjustments')):
            raise DomainError('INVALID_PARAMETERS', 'Без актуального складского снимка IEK доступен только прогноз продаж; загрузите current_stock_inbound по шаблону для политики и расчёта заказа.')
        origin = pd.Timestamp(request['as_of_date'])
        meta = {'calculation_id': calculation_id, 'dataset_id': dataset['dataset_id'],
                'dataset_version': dataset['dataset_version'], 'data_as_of': dataset['data_as_of'],
                'as_of_date': request['as_of_date'], 'mode': request['mode'], 'revision': 1,
                'policy_version': 'iek-forecast-only-v1', 'created_at': now(), 'horizon_days': 28,
                'issues': deepcopy(dataset['issues'])}
        details = {}
        for panel in panels:
            if origin > panel.daily.columns[-1]:
                raise DomainError('MISSING_CRITICAL_DATA', 'История IEK не доходит до даты расчёта.')
            frame, forecasts, entry, selection_hash = _load_forecast(panel, origin)
            corrections = regular_forecasts(panel, origin, context, events)
            if not np.isfinite(forecasts).all() or (forecasts < 0).any():
                raise DomainError('INVALID_PIPELINE_RESULT', 'Модель вернула некорректный прогноз.', 500)
            for position, sku in enumerate(frame.sku.astype(str)):
                value = float(forecasts[position])
                correction = corrections.get(sku)
                if correction:
                    value = correction['mean']
                item_id = 'iek-' + sha256((sku + '|' + panel.unit).encode()).hexdigest()[:12]
                item = {
                    'item_id': item_id, 'supplier_id': 'iek', 'supplier_name': 'IEK', 'sku': sku,
                    'supplier_article': None, 'name': names.get(sku, sku), 'unit': panel.unit,
                    'warehouse_id': 'almaty', 'category_raw': None, 'policy_basis': None,
                    'decision_status': 'needs_data', 'urgency': 'unknown', 'free_stock': None,
                    'inbound_within_horizon': None, 'material_requirement_uncovered': None,
                    'forecast': {'horizon_days': 28, 'period_start': str((origin + pd.Timedelta(days=1)).date()),
                                 'period_end': str((origin + pd.Timedelta(days=28)).date()),
                                 'method': 'baseline' if entry['selected'].startswith(('mean', 'median', 'snaive', 'croston', 'tsb')) else 'ml',
                                 'model_id': f"forecast-v2-{selection_hash[:12]}:{panel.name}", 'mean': value,
                                 'p10': None, 'p50': None, 'p90': None, 'target_quantile': None, 'target_stock': None,
                                 'calibration_status': 'insufficient_data',
                                 'note': f"Прогноз наблюдаемых продаж; метод {entry['selected']}. Интервалы не калиброваны."},
                    'min_order_qty': None, 'order_multiple': None, 'recommended_quantity': None,
                    'final_quantity': None, 'override_reason': None, 'unit_cost_kzt': None,
                    'order_cost_kzt': None, 'marginal_value': None, 'economics_source': None,
                    'reason': 'ML-прогноз готов. Для количества закупки нужен актуальный свободный остаток, условия поставки и политика запаса.',
                    'issues': [_issue('CURRENT_STOCK_REQUIRED', 'error', 'Нет актуального свободного остатка IEK.', [sku])],
                    'evidence': [{'id': item_id + '-forecast', 'source_kind': dataset['source_kind'],
                                  'reference': f"forecast-v2:{selection_hash}:{panel.name}",
                                  'label': 'Прогноз по наблюдаемым продажам на 28 дней', 'value': value, 'unit': panel.unit}],
                    'ai': {'status': 'not_requested', 'verdict': None, 'reasons': [], 'evidence_ids': [],
                           'rule_ids': [], 'suggested_action': None, 'provider_model': None},
                }
                if correction:
                    item['forecast'].update(model_id=correction['model_id'], note=correction['note'],
                                            method='baseline' if correction['audit']['selected_method'].startswith(('mean', 'median')) else 'ml')
                    item['issues'].append(_issue('REGULAR_DEMAND_CONTEXT', 'info', correction['note'], [sku], 'normalized/context.json'))
                    item['evidence'].extend([
                        {'id': item_id + '-client-exclusion', 'source_kind': dataset['source_kind'],
                         'reference': 'normalized/events.json', 'label': 'Исключённый разовый клиентский объём',
                         'value': correction['excluded_quantity'], 'unit': panel.unit},
                        {'id': item_id + '-stockout', 'source_kind': dataset['source_kind'],
                         'reference': 'normalized/context.json', 'label': 'Учтено дней отсутствия',
                         'value': correction['stockout_days'], 'unit': 'дней'},
                    ])
                details[item_id] = {'meta': meta, 'item': item, 'history': _history(panel, sku, origin, correction, dataset['source_kind']),
                                    'inbound': [], 'economic_profile': None, 'applied_rule_ids': ['DATA-01']}
        items = [detail['item'] for detail in details.values()]
        return {'response': {'meta': meta, 'summary': summary(items), 'items': items},
                'details': details, 'request': request, 'approval_constraints': {}}
