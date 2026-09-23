from io import BytesIO
from datetime import datetime

from openpyxl import Workbook
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.contracts import validate


def workbook(rows):
    book = Workbook()
    for row in rows:
        book.active.append(row)
    output = BytesIO()
    book.save(output)
    book.close()
    return output.getvalue()


def test_observed_iek_import_real_weights_and_no_invented_order(tmp_path, monkeypatch):
    monkeypatch.setenv('DATA_DIR', str(tmp_path))
    monkeypatch.delenv('TIREK_PIPELINE', raising=False)
    monkeypatch.delenv('TIREK_INGESTOR', raising=False)
    # Synthetic workbook contents exercise the real import and frozen weights.
    # They are not partner data or forecast-accuracy evidence.
    sales = workbook([
        ['Дата', 'Номер', 'Документ', 'Код', 'Номенклатура', 'Ед.', 'Склад', 'Количество'],
        [datetime(2025, 1, 1), 'TEST1', 'Расходная накладная TEST1', 'TEST-001', 'Тестовый товар', 'шт', 'Алматы', 10],
        [datetime(2026, 9, 22), 'TEST2', 'Расходная накладная TEST2', 'TEST-001', 'Тестовый товар', 'шт', 'Алматы', 20],
    ])
    months = ['янв', 'фев', 'мар', 'апр', 'май', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек']
    monthly = workbook([['Номенклатура', 'Номенклатура.Код', *[f'{month}. 2024' for month in months]],
                        ['Тестовый товар', 'TEST-001', *([30] * 12)]])
    with TestClient(create_app(tmp_path / 'test.sqlite3')) as client:
        uploaded = client.post('/api/v1/datasets/import', data={'supplier_id': 'iek'},
                               headers={'Idempotency-Key': 'upload'}, files=[
            ('files', ('Динамика продаж.xlsx', sales)), ('files', ('Ежемесячные продажи.xlsx', monthly)),
        ])
        job = client.get('/api/v1/jobs/' + uploaded.json()['job_id']).json()
        assert job['status'] == 'succeeded', job
        dataset = client.get('/api/v1/datasets/' + job['resource_id']).json()
        assert dataset['calculation_allowed'] and dataset['sku_count'] == 1
        assert dataset['source_kind'] == 'observed'
        payload = {'dataset_id': dataset['dataset_id'], 'as_of_date': '2026-09-22',
                   'warehouse_ids': ['almaty'], 'category_codes': [], 'horizon_days': 28,
                   'lead_time_days': 7, 'review_period_days': 21, 'mode': 'operational',
                   'category_policies': [], 'economic_profiles': [], 'growth_adjustments': [],
                   'budget_kzt': None, 'request_ai_review': False}
        created = client.post('/api/v1/calculations', json=payload, headers={'Idempotency-Key': 'predict'})
        job = client.get('/api/v1/jobs/' + created.json()['job_id']).json()
        assert job['status'] == 'succeeded', job
        result = client.get('/api/v1/calculations/' + job['resource_id'] + '/recommendations').json()
        validate('RecommendationsResponse', result)
        item = result['items'][0]
        assert item['forecast']['method'] == 'ml'
        assert item['forecast']['mean'] >= 0
        assert item['final_quantity'] is None and item['free_stock'] is None
        assert item['decision_status'] == 'needs_data'
        assert item['name'] == 'Тестовый товар'
