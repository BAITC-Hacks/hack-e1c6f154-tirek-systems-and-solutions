"""Explicit, deterministic synthetic fixtures. No model, ML metrics or LLM claims."""
from copy import deepcopy
from datetime import date, timedelta, datetime, timezone
from hashlib import sha256
import json
from .contracts import ROOT

ROLES = {
    'sales_transactions': 'Динамика продаж', 'sales_monthly': 'Ежемесячные продажи',
    'stock_monthly': 'Ежемесячные остатки', 'seasonality': 'Сезонность',
    'moq': 'Минимальные партии', 'current_stock_inbound': 'Товар в пути',
}

PRODUCTS = [
    ('EZ9F34116', 'Автоматический выключатель Easy9', '1P · 16 А · характеристика C', 'Автоматы', 24, 0, 168, 144, 12, 1450, 'critical'),
    ('EZ9F34325', 'Автоматический выключатель Easy9', '3P · 25 А · характеристика C', 'Автоматы', 36, 24, 132, 72, 6, 4800, 'high'),
    ('ATN000143', 'Розетка с заземлением AtlasDesign', 'Белый · 16 А · скрытый монтаж', 'Электроустановка', 80, 40, 216, 96, 12, 1250, 'normal'),
    ('A9R11225', 'Устройство защитного отключения', 'Acti9 · 2P · 25 А · 30 мА', 'Дифференциальная защита', 6, 0, 36, 30, 6, 12400, 'critical'),
    ('ATN000113', 'Выключатель одноклавишный', 'AtlasDesign · белый · 10 А', 'Электроустановка', 120, 0, 180, 60, 10, 980, 'normal'),
    ('LC1D09M7', 'Контактор TeSys D', '9 А · 220 В · 1НО + 1НЗ', 'Управление', 8, 6, 32, 18, 1, 18600, 'high'),
    ('EZ9E112S2F', 'Щит распределительный Easy9', '12 модулей · навесной · IP40', 'Корпуса', 12, 0, 32, 20, 1, 7900, 'normal'),
    ('A9F74110', 'Выключатель модульный Acti9', 'iC60N · 1P · 10 А · C', 'Автоматы', 24, 12, 60, 24, 12, 5300, 'low'),
    ('ATN000183', 'Рамка двухместная AtlasDesign', 'Белый · горизонтальный монтаж', 'Электроустановка', 40, 20, 120, 60, 10, None, 'normal'),
    ('EZ9F34106', 'Автоматический выключатель Easy9', '1P · 6 А · характеристика C', 'Автоматы', 180, 0, 96, 0, 12, 1420, 'low'),
    ('A9R41240', 'Дифференциальный выключатель', 'Acti9 · 2P · 40 А · 30 мА', 'Дифференциальная защита', None, 0, 42, None, 6, 14800, 'unknown'),
    ('LC1D18M7', 'Контактор TeSys D', '18 А · 220 В · 1НО + 1НЗ', 'Управление', 4, 0, 16, 12, None, 24500, 'high'),
]


def now():
    return datetime.now(timezone.utc).isoformat()


def summary(items):
    return {
        'item_count': len(items),
        **{key + '_count': sum(i['decision_status'] == key for i in items)
           for key in ('ready', 'no_order', 'needs_data', 'needs_review')},
        'known_order_cost_kzt': round(sum(i['order_cost_kzt'] or 0 for i in items), 2),
        'order_cost_complete': all(i['final_quantity'] is not None and (i['final_quantity'] == 0 or i['order_cost_kzt'] is not None) for i in items),
    }


def make_demo(calculation_id='demo-calc-001', request=None):
    sample = json.loads((ROOT / 'contracts/examples/item-detail.json').read_text(encoding='utf-8'))
    as_of = date.fromisoformat((request or {}).get('as_of_date', '2026-09-22'))
    meta = {**sample['meta'], 'calculation_id': calculation_id, 'dataset_id': 'demo-systeme-v1',
            'dataset_version': 'scenario-2026.09-v1', 'as_of_date': str(as_of),
            'created_at': now(), 'policy_version': 'demo-fixture-v1'}
    details = {}
    for index, (sku, name, subtitle, category, stock, inbound, target, quantity, multiple, cost, urgency) in enumerate(PRODUCTS):
        item = deepcopy(sample['item'])
        item_id = f'se-{sku.lower()}'
        status = 'needs_data' if stock is None else 'needs_review' if multiple is None else 'no_order' if quantity == 0 else 'ready'
        item.update(item_id=item_id, supplier_id='systeme-electric', supplier_name='Systeme Electric',
                    sku=sku, supplier_article=sku, name=f'{name}, {subtitle}', category_raw=category,
                    warehouse_id='almaty', free_stock=stock, inbound_within_horizon=inbound,
                    recommended_quantity=quantity, final_quantity=quantity, min_order_qty=multiple,
                    order_multiple=multiple, unit_cost_kzt=cost,
                    order_cost_kzt=None if cost is None or quantity is None else quantity * cost,
                    decision_status=status, urgency=urgency,
                    policy_basis='economic' if cost else 'service_policy', economics_source='synthetic' if cost else None)
        item['forecast'].update(mean=round(target * .86), p10=round(target * .62), p50=round(target * .84),
                                p90=target, target_stock=target, target_quantile=.9,
                                period_start=str(as_of + timedelta(days=1)), period_end=str(as_of + timedelta(days=28)))
        item['reason'] = ('Остатка достаточно на выбранный горизонт. Дополнительная закупка не требуется.' if quantity == 0 else
                          'Нет актуального свободного остатка. Добавьте данные, чтобы определить количество.' if stock is None else
                          f'Целевой запас {target} шт., доступно {stock} шт., в пути {inbound} шт. ' +
                          ('Уточните минимальную партию и кратность у поставщика.' if multiple is None else f'Партия согласована с кратностью {multiple} шт.'))
        item['reason'] += ' Синтетический сценарий.'
        item['issues'] = []
        if stock is None or multiple is None:
            item['issues'] = [{'code': 'MISSING_CURRENT_STOCK' if stock is None else 'UNKNOWN_ORDER_MULTIPLE',
                               'severity': 'error' if stock is None else 'warning',
                               'message': 'Нет актуального остатка.' if stock is None else 'Кратность поставки не подтверждена.',
                               'affected_skus': [sku], 'source_reference': 'synthetic-fixture'}]
        item['evidence'] = [{'id': f'{item_id}-{key}', 'source_kind': 'synthetic', 'reference': 'demo.py / PRODUCTS',
                             'label': label, 'value': value, 'unit': 'шт'} for key, label, value in
                            [('stock', 'Свободный остаток', stock), ('inbound', 'Поступление', inbound), ('target', 'Целевой запас', target)]]
        if request and request.get('request_ai_review'):
            item['ai'].update(status='unavailable', reasons=['LLM-провайдер пока не подключён.'])
        history = []
        for n, factor in enumerate([.54, .62, .57, .72, .68, .79]):
            end = as_of - timedelta(days=(5 - n) * 28)
            sales = round(target * factor + (index % 3) * 2)
            history.append({'period_start': str(end - timedelta(days=27)), 'period_end': str(end),
                            'observed_sales': sales, 'regular_sales': sales, 'estimated_lost_demand': None, 'source_kind': 'synthetic'})
        details[item_id] = {'meta': meta, 'item': item, 'history': history,
                            'inbound': [{'order_id': f'demo-inbound-{index}', 'quantity': inbound,
                                         'expected_at': str(as_of + timedelta(days=4)), 'source_reference': 'synthetic-fixture'}] if inbound else [],
                            'economic_profile': {'sku': sku, 'underage_cost': cost * .9, 'overage_cost': cost * .1,
                                                 'unit_cost': cost, 'currency': 'KZT', 'horizon_days': 28,
                                                 'source_kind': 'synthetic', 'rationale': 'Индивидуальный синтетический профиль для демонстрации.'} if cost else None,
                            'applied_rule_ids': ['scenario-fixture', 'order-multiple']}
    if request:
        details = {k: v for k, v in details.items()
                   if (not request['warehouse_ids'] or v['item']['warehouse_id'] in request['warehouse_ids'])
                   and (not request['category_codes'] or v['item']['category_raw'] in request['category_codes'])}
    items = [v['item'] for v in details.values()]
    return {'response': {'meta': meta, 'summary': summary(items), 'items': items}, 'details': details,
            'request': request or {'budget_kzt': None}}


def demo_dataset():
    return {'dataset_id': 'demo-systeme-v1', 'dataset_version': 'scenario-2026.09-v1', 'data_as_of': '2026-09-22',
            'timezone': 'Asia/Almaty', 'source_kind': 'synthetic', 'supplier_ids': ['systeme-electric'],
            'sku_count': len(PRODUCTS), 'calculation_allowed': True,
            'sources': [{'role': role, 'filename': f'demo-{role}.xlsx', 'sha256': sha256(role.encode()).hexdigest(),
                         'rows_read': len(PRODUCTS), 'rows_used': len(PRODUCTS), 'data_as_of': '2026-09-22'} for role in ROLES],
            'issues': [{'code': 'SYNTHETIC_DATA', 'severity': 'warning', 'message': 'Демонстрационный набор. Файлы и значения синтетические, модель не запускалась.',
                        'affected_skus': [], 'source_reference': 'backend/app/demo.py'}]}
