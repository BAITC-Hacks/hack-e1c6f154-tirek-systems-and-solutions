from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest

from backend.app.web import install_frontend


def test_spa_fallback_does_not_hide_api_or_missing_assets(tmp_path, monkeypatch):
    (tmp_path / 'index.html').write_text('<html>Tirek</html>', encoding='utf-8')
    monkeypatch.setenv('TIREK_STATIC_DIR', str(tmp_path))
    app = FastAPI()
    install_frontend(app)
    with TestClient(app) as client:
        assert client.get('/recommendations').text == '<html>Tirek</html>'
        assert client.get('/api/v1/nonexistent').status_code == 404
        assert client.get('/assets/missing.js').status_code == 404
        assert client.get('/%2e%2e/secret').status_code == 404
        assert client.get('/').headers['X-Frame-Options'] == 'DENY'


def test_unsafe_static_paths_are_rejected_before_any_filesystem_resolution(tmp_path, monkeypatch):
    (tmp_path / 'index.html').write_text('<html>Tirek</html>', encoding='utf-8')
    monkeypatch.setenv('TIREK_STATIC_DIR', str(tmp_path))
    app = FastAPI()
    install_frontend(app)
    endpoint = next(route.endpoint for route in app.routes if route.path == '/{path:path}')

    def unexpected_resolution(*args, **kwargs):
        pytest.fail('An unsafe client path reached filesystem resolution')

    monkeypatch.setattr(Path, 'resolve', unexpected_resolution)
    for path in ('\\\\review-example.invalid\\share\\asset', '//review-example.invalid/share/asset',
                 'C:/private/secret', 'C:private', 'assets\\..\\secret',
                 '../secret', 'assets/../../secret', '/private/file', 'index.html:stream', 'asset\x00'):
        with pytest.raises(HTTPException) as blocked:
            endpoint(path)
        assert blocked.value.status_code == 404


def test_encoded_unc_and_drive_paths_cannot_reach_static_files(tmp_path, monkeypatch):
    (tmp_path / 'index.html').write_text('<html>Tirek</html>', encoding='utf-8')
    monkeypatch.setenv('TIREK_STATIC_DIR', str(tmp_path))
    app = FastAPI()
    install_frontend(app)
    with TestClient(app) as client:
        for path in ('/%5c%5creview-example.invalid%5cshare%5casset', '/C%3a/private/secret',
                     '/assets/%2e%2e/secret', '/index.html%3astream'):
            assert client.get(path).status_code == 404
