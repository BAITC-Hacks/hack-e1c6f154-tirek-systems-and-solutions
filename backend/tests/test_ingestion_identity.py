"""Dataset identity includes filename metadata used to date SE snapshots."""
from io import BytesIO

from openpyxl import Workbook

from backend.app.ingestion import inspect_upload


def workbook_bytes():
    workbook = Workbook()
    workbook.active.append(['sku', 'quantity'])
    workbook.active.append(['000123', 12])
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def test_dated_filename_changes_dataset_identity_for_identical_workbook_bytes():
    content = workbook_bytes()
    first = inspect_upload([('Товар в пути на 22.09.2026.xlsx', content)],
                           'systeme-electric', None)
    next_snapshot = inspect_upload([('Товар в пути на 23.09.2026.xlsx', content)],
                                   'systeme-electric', None)
    repeated = inspect_upload([('Товар в пути на 22.09.2026.xlsx', content)],
                              'systeme-electric', None)

    assert first['sources'][0]['sha256'] == next_snapshot['sources'][0]['sha256']
    assert first['sources'][0]['role'] == next_snapshot['sources'][0]['role']
    assert first['dataset_id'] != next_snapshot['dataset_id']
    assert first['dataset_version'] != next_snapshot['dataset_version']
    assert repeated['dataset_id'] == first['dataset_id']
    assert repeated['dataset_version'] == first['dataset_version']


def test_upload_order_does_not_change_dataset_identity():
    content = workbook_bytes()
    files = [('Товар в пути на 22.09.2026.xlsx', content), ('MOQ SystemElectric.xlsx', content)]
    first = inspect_upload(files, 'systeme-electric', None)
    reversed_files = inspect_upload(list(reversed(files)), 'systeme-electric', None)

    assert reversed_files['dataset_id'] == first['dataset_id']
    assert reversed_files['dataset_version'] == first['dataset_version']
