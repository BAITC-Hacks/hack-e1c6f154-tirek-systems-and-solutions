"""Malformed explicit stock cannot become a confirmed IEK purchasing input."""
from datetime import date

import pytest
from openpyxl import Workbook

from backend.app.inventory_template import HEADERS, read_current_stock
from model.decision.core import (
    InventorySnapshot, RecommendationInput, ServicePolicy, SupplierConstraint, recommend,
)


def stock_row(**updates):
    row = dict(zip(HEADERS, (
        '001', 'Synthetic test product', 'шт', 'almaty', '2026-09-22',
        20, 5, 15, 5, 5, 1, 'test-category', 100, 10, '2026-09-24',
    )))
    row.update(updates)
    return row


def workbook(tmp_path, rows):
    path = tmp_path / 'current_stock_inbound.SYNTHETIC.xlsx'
    book = Workbook()
    book.active.title = 'Stock'
    book.active.append(HEADERS)
    for row in rows:
        book.active.append([row[name] for name in HEADERS])
    book.save(path)
    book.close()
    return path


def test_duplicate_sku_does_not_silently_overwrite_inventory(tmp_path):
    path = workbook(tmp_path, [stock_row(), stock_row(**{'Код': ' 001 ', 'Свободный остаток': 999})])
    with pytest.raises(ValueError, match='Duplicate current stock SKU'):
        read_current_stock(path)


@pytest.mark.parametrize('warehouse', ['astana', None])
def test_wrong_or_missing_warehouse_cannot_confirm_almaty_scope(tmp_path, warehouse):
    path = workbook(tmp_path, [stock_row(**{'Склад': warehouse})])
    with pytest.raises(ValueError, match='supports warehouse'):
        read_current_stock(path)


@pytest.mark.parametrize('unit', ['кг', None])
def test_unknown_unit_is_not_assumed_to_mean_pieces(tmp_path, unit):
    path = workbook(tmp_path, [stock_row(**{'Ед.': unit})])
    with pytest.raises(ValueError, match='unit must be'):
        read_current_stock(path)


def test_conflicting_free_stock_balance_is_rejected(tmp_path):
    path = workbook(tmp_path, [stock_row(**{'Свободный остаток': 16})])
    with pytest.raises(ValueError, match='free balance contradicts'):
        read_current_stock(path)


@pytest.mark.parametrize('eta', [None, '2026-09-21'])
def test_positive_inbound_needs_current_explicit_eta(tmp_path, eta):
    path = workbook(tmp_path, [stock_row(**{'Дата поступления': eta})])
    with pytest.raises(ValueError, match='requires an explicit ETA'):
        read_current_stock(path)


def test_confirmed_zero_inbound_does_not_require_eta(tmp_path):
    profile = read_current_stock(workbook(tmp_path, [stock_row(**{'В пути': 0, 'Дата поступления': None})]))['001']
    assert profile['inbound_quantity'] == 0
    assert profile['inbound_eta'] is None


def test_missing_quantum_stays_unknown_and_actual_decision_core_blocks_order(tmp_path):
    profile = read_current_stock(workbook(tmp_path, [stock_row(**{'Шаг единицы': None})]))['001']
    assert profile['unit_quantum'] is None
    # Exercise the real business rule: importing a partial source is allowed,
    # but its missing physical quantum cannot produce an approvable quantity.
    result = recommend(RecommendationInput(
        sku=profile['sku'], warehouse_id='almaty', supplier_id='iek', unit=profile['unit'],
        as_of=date(2026, 9, 22), forecast_as_of=date(2026, 9, 22),
        scenarios=[[10.0] * 28], forecast_id='synthetic-test-forecast',
        unit_quantum=profile['unit_quantum'],
        inventory=InventorySnapshot(date(2026, 9, 22), profile['on_hand'], profile['reserved'], profile['free_stock']),
        lead_time_days=7, review_period_days=21,
        constraints=SupplierConstraint('шт', profile['min_order_qty'], profile['order_multiple'], 1, 'synthetic-template'),
        service_policy=ServicePolicy(.9, 'Synthetic test policy', 'test-v1'),
    ))
    assert result.status == 'needs_data'
    assert result.quantity is None
    assert 'unit_quantum' in result.required_fields


@pytest.mark.parametrize('quantum', [0, -1])
def test_explicit_invalid_quantum_is_rejected(tmp_path, quantum):
    path = workbook(tmp_path, [stock_row(**{'Шаг единицы': quantum})])
    with pytest.raises(ValueError, match='invalid negative or zero quantity'):
        read_current_stock(path)
