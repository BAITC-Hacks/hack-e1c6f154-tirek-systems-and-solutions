import pytest

from backend.app.contracts import DomainError
from backend.app import runtime


def test_capabilities_do_not_claim_missing_adapter_is_connected(monkeypatch):
    monkeypatch.setenv('TIREK_PIPELINE', 'missing_tirek_adapter:Pipeline')
    result = runtime.capabilities()
    assert result['ml_connected'] is False
    assert result['ml_error']
    with pytest.raises(DomainError) as error:
        runtime.load_adapter('missing_tirek_adapter:Pipeline')
    assert error.value.code == 'ADAPTER_UNAVAILABLE'
    assert error.value.status == 503


def test_missing_weights_report_a_runtime_problem(tmp_path, monkeypatch):
    monkeypatch.delenv('TIREK_PIPELINE', raising=False)
    monkeypatch.delenv('TIREK_INGESTOR', raising=False)
    monkeypatch.setattr(runtime, 'ROOT', tmp_path)
    result = runtime.capabilities()
    assert result['ml_connected'] is False
    assert result['real_import'] == 'normalized'
    assert 'артефакты' in result['ml_error']
