from io import BytesIO
from zipfile import ZipFile

from fastapi.testclient import TestClient
import pytest

from backend.app.main import create_app


@pytest.mark.parametrize('supplier,count', [('systeme-electric', 6), ('iek', 3)])
def test_generated_sample_is_downloadable_and_only_synthetic(tmp_path, monkeypatch, supplier, count):
    monkeypatch.setenv('DATA_DIR', str(tmp_path))
    monkeypatch.setenv('TIREK_AUTH_DISABLED', '1')
    with TestClient(create_app(tmp_path / 'test.sqlite3')) as client:
        response = client.get('/api/v1/samples', params={'supplier_id': supplier})
        assert response.status_code == 200
        assert response.headers['content-type'] == 'application/zip'
        with ZipFile(BytesIO(response.content)) as archive:
            workbooks = [name for name in archive.namelist() if name.endswith('.xlsx')]
            assert len(workbooks) == count
            assert all('SYNTHETIC' in name for name in workbooks)
            assert 'additional-context.SYNTHETIC.json' in archive.namelist()
            assert all('/' not in name and '\\' not in name for name in archive.namelist())
        assert client.get('/api/v1/samples?supplier_id=unknown').status_code == 422
