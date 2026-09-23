"""Generated XLSX -> actual normalizer + frozen weights + core + valid order.

This test uses no forecast mocks, private exports, API key, or external service.
It is functional integration evidence, not evidence of real-world ML accuracy.
"""
import json

from backend.app.contracts import validate
from backend.app.ingestion import inspect_upload
from backend.app.ml_adapter import normalize_dataset
from backend.app.pipeline import calculate
from scripts.generate_sample_data import generate, generate_iek


def test_complete_synthetic_uploaded_order_uses_real_pipeline(tmp_path, monkeypatch):
    monkeypatch.setenv('DATA_DIR', str(tmp_path))
    paths = generate(tmp_path / 'source')
    context = json.loads(paths['context'].read_text(encoding='utf-8'))
    files = [(path.name, path.read_bytes()) for role, path in paths.items() if role not in ('context', 'request')]
    report = inspect_upload(files, 'systeme-electric', context)
    directory = tmp_path / 'uploads' / report['dataset_id']
    directory.mkdir(parents=True)
    for source in report['sources']:
        (directory / (source['role'] + '.xlsx')).write_bytes(paths[source['role']].read_bytes())
    report = normalize_dataset(directory, report, context)
    validate('DatasetReport', report)
    assert report['source_kind'] == 'synthetic'
    assert report['calculation_allowed'] and report['sku_count'] == 4
    assert not {'UNKNOWN_MIN_ORDER_QTY', 'UNVERIFIED_WAREHOUSE_SCOPE'} & {i['code'] for i in report['issues']}
    request = json.loads(paths['request'].read_text(encoding='utf-8'))
    request['dataset_id'] = report['dataset_id']
    result = calculate(report, request, 'actual-synthetic-import')
    items = {item['sku']: item for item in result['response']['items']}
    assert set(items) == {'SYN-REG-001', 'SYN-OUT-002', 'SYN-ONE-003', 'SYN-SEASON-004'}
    for item in items.values():
        assert item['final_quantity'] is not None
        assert item['final_quantity'] % item['order_multiple'] == 0
        assert item['decision_status'] in ('ready', 'no_order', 'needs_review'), item['issues']
        assert result['approval_constraints'][item['item_id']]['constraints_complete']
    assert items['SYN-REG-001']['forecast']['model_id'].startswith('forecast-v2-')
    assert items['SYN-OUT-002']['forecast']['model_id'].startswith('regular-poisson-')
    oneoff = next(e for e in items['SYN-ONE-003']['evidence'] if e['id'].endswith('-client-exclusion'))
    assert oneoff['value'] == 300
    assert items['SYN-OUT-002']['unit_cost_kzt'] == 1000
    assert result['response']['summary']['order_cost_complete']


def test_explicit_iek_stock_allows_orders_in_three_separate_units(tmp_path, monkeypatch):
    monkeypatch.setenv('DATA_DIR', str(tmp_path))
    paths = generate_iek(tmp_path / 'iek')
    files = [(path.name, path.read_bytes()) for role, path in paths.items() if role not in ('context', 'request')]
    report = inspect_upload(files, 'iek', {})
    directory = tmp_path / 'uploads' / report['dataset_id']
    directory.mkdir(parents=True)
    for source in report['sources']:
        (directory / (source['role'] + '.xlsx')).write_bytes(paths[source['role']].read_bytes())
    report = normalize_dataset(directory, report, {})
    assert 'FORECAST_ONLY' not in {issue['code'] for issue in report['issues']}
    assert report['source_kind'] == 'synthetic'
    request = json.loads(paths['request'].read_text(encoding='utf-8'))
    request['dataset_id'] = report['dataset_id']
    result = calculate(report, request, 'iek-stock-and-weights')
    assert len(result['response']['items']) == 3
    assert {item['unit'] for item in result['response']['items']} == {'шт', 'м', 'упак'}
    for item in result['response']['items']:
        assert item['supplier_id'] == 'iek'
        assert item['final_quantity'] is not None and item['final_quantity'] > 0, item
        assert item['final_quantity'] % item['order_multiple'] == 0
        assert result['approval_constraints'][item['item_id']]['constraints_complete']
        assert item['order_cost_kzt'] == item['final_quantity'] * 500
    assert result['response']['summary']['order_cost_complete']
