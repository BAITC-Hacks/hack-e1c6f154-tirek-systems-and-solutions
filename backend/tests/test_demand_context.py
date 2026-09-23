"""Interventions on observed inputs, not synthetic forecast accuracy claims."""
from copy import deepcopy
from datetime import datetime

import numpy as np
import pandas as pd
import pytest
from openpyxl import Workbook

from backend.app.demand_context import normalize_events, regular_forecasts, validate_intervals
from model.forecast_v2.data import Panel


def panel():
    dates = pd.date_range('2026-01-01', '2026-09-22')
    return Panel('SE__pieces', 'SE', 'шт', 'Алматы',
                 pd.DataFrame(10.0, index=['001'], columns=dates), pd.DataFrame(), {})


def event(stamp='2026-01-01', quantity=10, client='regular', event_id='sales:2'):
    return {'event_id': event_id, 'date': stamp, 'quantity': quantity, 'client_id': client,
            'sku': '001', 'unit': 'шт', 'warehouse_id': 'almaty'}


def interval(start='2026-09-01', end='2026-09-14'):
    return {'sku': '001', 'warehouse_id': 'almaty', 'start_date': start, 'end_date': end,
            'source_kind': 'synthetic', 'reference': 'explicit-test-interval'}


def test_stockout_adjustment_increases_forecast_on_same_observed_sales():
    data = panel()
    data.daily.loc['001', '2026-01-01'::2] = 0
    intervals = [interval(str(day.date()), str(day.date())) for day in data.daily.columns[::2]]
    # Keep one innocuous labeled event so both paths estimate regular demand.
    events = [event('2026-01-02')]
    before = regular_forecasts(data, '2026-09-22', {}, events)['001']
    after = regular_forecasts(data, '2026-09-22', {'stockout_intervals': intervals}, events)['001']
    assert after['mean'] > before['mean'] * 1.7
    assert after['stockout_days'] == len(intervals)
    assert after['mean'] == pytest.approx(280, rel=.08)


def test_labeled_oneoff_is_excluded_but_observed_ledger_is_preserved():
    data = panel()
    data.daily.loc['001', '2026-09-10'] += 1000
    original = data.daily.copy(deep=True)
    ordinary = [event()]
    before = regular_forecasts(data, '2026-09-22', {}, ordinary)['001']
    after = regular_forecasts(data, '2026-09-22', {}, ordinary + [event('2026-09-10', 1000, 'oneoff', 'sales:999')])['001']
    assert after['excluded_quantity'] == 1000
    assert after['mean'] < before['mean']
    assert after['regular_history']['2026-09-10'] == 10
    pd.testing.assert_frame_equal(data.daily, original)


def test_no_context_does_not_claim_regular_demand():
    assert regular_forecasts(panel(), '2026-09-22', {}, []) == {}


def test_stockout_conflict_reversed_dates_and_unknown_sku_rejected():
    data = panel()
    for value in (interval(), interval('2026-09-15', '2026-09-01'),
                  interval('2026-09-22', '2026-09-30'), {**interval(), 'sku': 'missing'}):
        with pytest.raises(ValueError):
            validate_intervals({'stockout_intervals': [value]}, [data])


def test_foreign_warehouse_context_does_not_modify_almaty():
    data = panel()
    context = {'stockout_intervals': [{**interval(), 'warehouse_id': 'astana'}]}
    assert regular_forecasts(data, '2026-09-22', context, [{**event(), 'warehouse_id': 'astana'}]) == {}


def test_future_event_cannot_influence_past_prediction():
    data = panel()
    before = regular_forecasts(data, '2026-08-31', {}, [event()])
    after = regular_forecasts(data, '2026-08-31', {}, [event(), event('2026-09-10', 1000, 'future', 'sales:future')])
    assert before == after


def test_excel_row_labels_resolve_and_unknown_or_duplicate_labels_fail(tmp_path):
    path = tmp_path / 'sales.xlsx'
    book = Workbook()
    book.active.title = 'TDSheet'
    book.active.append(['Дата', 'Номер', 'Документ', 'Код', 'Номенклатура', 'Ед.', 'Склад', 'Количество'])
    book.active.append([datetime(2026, 9, 10), 'D1', 'Расходная накладная D1', '001', 'Товар', 'шт', 'Алматы', 100])
    book.save(path)
    book.close()
    label = {'source_event_id': 'sales_transactions:TDSheet:2', 'pseudonymous_client_id': 'anonymous-client'}
    events = normalize_events(path, {'client_labels': [label]})
    assert events[0]['sku'] == '001' and events[0]['quantity'] == 100
    assert events[0]['client_id'] == 'anonymous-client'
    with pytest.raises(ValueError, match='Unknown client'):
        normalize_events(path, {'client_labels': [{**label, 'source_event_id': 'not-a-row'}]})
    with pytest.raises(ValueError, match='Duplicate client'):
        normalize_events(path, {'client_labels': [label, label]})
