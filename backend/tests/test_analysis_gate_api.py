"""件数・IDの条件でAI呼出を抑制することをHTTP経由で検証する。"""

from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app
from app.schemas.analysis import AnalyzeResponse
from app.services.analysis_service import AnalysisError


def payload(end, start=1, session="call-1"):
    return {"sessionId": session, "utterances": [
        {"id": f"u{i}", "text": f"発言{i}", "startSec": i}
        for i in range(start, end + 1)
    ]}


@pytest.fixture
def gate_client(monkeypatch, tmp_path):
    monkeypatch.setenv("BACKEND_API_KEY", "gate-key")
    monkeypatch.setenv("ANALYSIS_STATE_BACKEND", "sqlite")
    monkeypatch.setenv("ANALYSIS_STATE_PATH", str(tmp_path / "state.sqlite3"))
    monkeypatch.setenv("ANALYSIS_CONTEXT_UTTERANCES", "5")
    get_settings.cache_clear()
    analyze = AsyncMock(return_value=AnalyzeResponse(
        is_suspicious=False, risk_level="low", category=None, quote=None,
        reason="根拠なし", advice="不明点を確認",
    ))
    monkeypatch.setattr("app.api.analyze.AnalysisService.analyze", analyze)
    with TestClient(create_app(), headers={"X-API-Key": "gate-key"}) as client:
        yield client, analyze
    get_settings.cache_clear()


def test_first_call_waits_until_five_then_all_five_ids_must_change(gate_client):
    client, analyze = gate_client
    for count in range(1, 5):
        response = client.post("/analyze", json=payload(count))
        assert response.status_code == 204
        assert response.content == b""
    analyze.assert_not_awaited()
    assert client.post("/analyze", json=payload(5)).status_code == 200
    for count in range(5, 10):
        assert client.post("/analyze", json=payload(count)).status_code == 204
    assert analyze.await_count == 1
    assert client.post("/analyze", json=payload(10)).status_code == 200
    assert analyze.await_count == 2


def test_after_first_success_a_five_item_window_is_accepted(gate_client):
    client, analyze = gate_client
    assert client.post("/analyze", json=payload(6)).status_code == 200
    assert client.post("/analyze", json=payload(11, start=7)).status_code == 200
    assert analyze.await_count == 2


def test_sessions_do_not_share_analyzed_ids(gate_client):
    client, analyze = gate_client
    assert client.post("/analyze", json=payload(6, session="a")).status_code == 200
    assert client.post("/analyze", json=payload(6, session="b")).status_code == 200
    assert analyze.await_count == 2


def test_failed_analysis_can_be_retried(gate_client):
    client, analyze = gate_client
    valid = analyze.return_value
    analyze.side_effect = [AnalysisError(504, "AI analysis timed out"), valid]
    assert client.post("/analyze", json=payload(6)).status_code == 504
    assert client.post("/analyze", json=payload(6)).status_code == 200
    assert client.post("/analyze", json=payload(6)).status_code == 204
    assert analyze.await_count == 2


def test_duplicate_window_ids_are_rejected(gate_client):
    client, analyze = gate_client
    value = payload(6)
    value["utterances"][-1]["id"] = "u5"
    assert client.post("/analyze", json=value).status_code == 422
    analyze.assert_not_awaited()


def test_missing_key_is_not_treated_as_waiting(gate_client):
    client, analyze = gate_client
    assert client.post(
        "/analyze", headers={"X-API-Key": "wrong"}, json=payload(2),
    ).status_code == 401
    analyze.assert_not_awaited()


def test_context_size_changes_first_and_subsequent_thresholds(gate_client, monkeypatch):
    client, analyze = gate_client
    monkeypatch.setenv("ANALYSIS_CONTEXT_UTTERANCES", "2")
    get_settings.cache_clear()
    assert client.post("/analyze", json=payload(1)).status_code == 204
    assert client.post("/analyze", json=payload(2)).status_code == 200
    assert client.post("/analyze", json=payload(3)).status_code == 204
    assert client.post("/analyze", json=payload(4)).status_code == 200
    assert analyze.await_count == 2


def test_firestore_failure_never_falls_back_or_calls_ai(gate_client, monkeypatch):
    from google.api_core.exceptions import PermissionDenied

    client, analyze = gate_client
    monkeypatch.setenv("ANALYSIS_STATE_BACKEND", "firestore")
    get_settings.cache_clear()
    factory = Mock(
        side_effect=PermissionDenied("private upstream message"),
    )
    monkeypatch.setattr("app.api.analyze.FirestoreAnalysisWindowStore", factory)
    response = client.post("/analyze", json=payload(6))
    assert response.status_code == 503
    assert response.json() == {"detail": "Analysis state store unavailable"}
    analyze.assert_not_awaited()


def test_three_disjoint_five_utterance_batches_are_each_analyzed_once(gate_client):
    client, analyze = gate_client
    for start in (1, 6, 11):
        value = payload(start + 4, start=start)
        assert client.post("/analyze", json=value).status_code == 200
        assert client.post("/analyze", json=value).status_code == 204
    assert analyze.await_count == 3
    for call, start in zip(analyze.await_args_list, (1, 6, 11), strict=True):
        assert [u.id for u in call.args[0].utterances] == [
            f"u{i}" for i in range(start, start + 5)
        ]
