"""分析から#17の保存・レポート取得までのHTTP結合テスト。"""

from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient
from google.api_core.exceptions import ServiceUnavailable

from app.api.v1.routes import get_session_service
from app.core.config import get_settings
from app.main import create_app
from app.schemas.analysis import AnalyzeResponse
from app.services.session_service import SessionService


@pytest.fixture
def storage_client(monkeypatch, tmp_path):
    monkeypatch.setenv("BACKEND_API_KEY", "storage-key")
    monkeypatch.setenv("ANALYSIS_STATE_BACKEND", "sqlite")
    monkeypatch.setenv("ANALYSIS_STATE_PATH", str(tmp_path / "state.sqlite3"))
    monkeypatch.setenv("ANALYSIS_CONTEXT_UTTERANCES", "5")
    get_settings.cache_clear()
    service = SessionService()
    factory = Mock(return_value=service)
    monkeypatch.setattr("app.api.analyze.get_session_service", factory)
    analyze = AsyncMock(return_value=AnalyzeResponse(
        is_suspicious=True, risk_level="high", category="C1",
        quote="今すぐ振り込んで", reason="送金を急がせる要求", advice="公式窓口に確認",
    ))
    monkeypatch.setattr("app.api.analyze.AnalysisService.analyze", analyze)
    app = create_app()
    app.dependency_overrides[get_session_service] = lambda: service
    with TestClient(app, headers={"X-API-Key": "storage-key"}) as client:
        yield client, service, analyze, factory


def payload(session="call"):
    return {"sessionId": session, "utterances": [
        {"id": f"u{i}", "text": "今すぐ振り込んで" if i == 2 else f"発言{i}",
         "startSec": i * 1.5}
        for i in range(5)
    ]}


@pytest.mark.parametrize("level,score,verdict", [
    ("low", 0.25, "low"),
    ("medium", 0.5, "suspicious"), ("high", 0.85, "likely_fake"),
])
def test_analyze_saves_before_return_and_report_contains_detection(
    storage_client, level, score, verdict,
):
    client, _, analyze, _ = storage_client
    analyze.return_value.risk_level = level
    assert client.post("/api/v1/sessions", json={"session_id": "call"}).status_code == 201
    response = client.post("/analyze", json=payload())
    assert response.status_code == 200
    assert response.json() == analyze.return_value.model_dump(mode="json")
    report = client.get("/api/v1/sessions/call").json()
    assert report["detections_count"] == 1
    assert report["max_risk_score"] == score
    assert report["overall_verdict"] == verdict
    item = report["detections"][0]
    assert item["timestamp_sec"] == 3.0
    assert item["quote"] == "今すぐ振り込んで"
    assert item["risk_level"] == verdict
    assert item["expire_at"] == report["expire_at"]
    assert client.post("/analyze", json=payload()).status_code == 204
    assert analyze.await_count == 1
    assert client.get("/api/v1/sessions/call").json()["detections_count"] == 1


def test_no_concern_does_not_initialize_storage(storage_client):
    client, _, analyze, factory = storage_client
    analyze.return_value = AnalyzeResponse(
        is_suspicious=False, risk_level="low", category=None, quote=None,
        reason="根拠なし", advice="不明点を確認",
    )
    assert client.post("/analyze", json=payload()).status_code == 200
    factory.assert_not_called()


def test_low_concern_does_not_downgrade_a_high_session(storage_client):
    client, service, analyze, _ = storage_client
    service.create_session("call")
    assert client.post("/analyze", json=payload()).status_code == 200
    analyze.return_value.risk_level = "low"
    value = payload()
    for utterance in value["utterances"]:
        utterance["id"] += "-next"
        utterance["startSec"] += 10
    assert client.post("/analyze", json=value).status_code == 200
    report = client.get("/api/v1/sessions/call").json()
    assert report["detections_count"] == 2
    assert report["max_risk_score"] == 0.85
    assert report["overall_verdict"] == "likely_fake"
    assert report["detections"][1]["risk_level"] == "low"


def test_missing_session_is_retryable_after_creation(storage_client):
    client, _, _, _ = storage_client
    assert client.post("/analyze", json=payload()).status_code == 404
    client.post("/api/v1/sessions", json={"session_id": "call"})
    assert client.post("/analyze", json=payload()).status_code == 200


def test_storage_failure_does_not_mark_analysis_completed(storage_client):
    client, service, _, factory = storage_client
    service.create_session("call")
    factory.side_effect = ServiceUnavailable("private upstream detail")
    response = client.post("/analyze", json=payload())
    assert response.status_code == 503
    assert response.json() == {"detail": "Detection storage unavailable"}
    factory.side_effect = None
    assert client.post("/analyze", json=payload()).status_code == 200


def test_retry_after_save_committed_does_not_duplicate_detection(storage_client, monkeypatch):
    client, service, _, _ = storage_client
    service.create_session("call")
    record = service.record_detection

    def save_then_fail(*args):
        record(*args)
        raise ServiceUnavailable("response lost after commit")

    monkeypatch.setattr(service, "record_detection", save_then_fail)
    assert client.post("/analyze", json=payload()).status_code == 503
    monkeypatch.setattr(service, "record_detection", record)
    assert client.post("/analyze", json=payload()).status_code == 200
    assert service.get_session("call").detections_count == 1


@pytest.mark.parametrize("quote", ["存在しない引用", "発言", "長" * 1001])
def test_invalid_or_ambiguous_result_is_not_saved(storage_client, quote):
    client, service, analyze, factory = storage_client
    service.create_session("call")
    analyze.return_value.quote = quote
    value = payload()
    if len(quote) > 1000:
        value["utterances"][2]["text"] = quote
    response = client.post("/analyze", json=value)
    assert response.status_code == 502
    assert response.json() == {"detail": "Analysis result cannot be saved"}
    factory.assert_not_called()
    assert service.get_session("call").detections_count == 0


def test_same_utterance_ids_are_saved_in_separate_sessions(storage_client):
    client, service, _, _ = storage_client
    for session in ("first", "second"):
        service.create_session(session)
        assert client.post("/analyze", json=payload(session)).status_code == 200
        assert service.get_session(session).detections_count == 1
    assert (service.get_session("first").detections[0].id
            != service.get_session("second").detections[0].id)


def test_firestore_initialization_failure_does_not_fall_back_to_memory(monkeypatch):
    monkeypatch.setenv("GCP_PROJECT_ID", "test-project")
    get_settings.cache_clear()
    get_session_service.cache_clear()
    constructor = Mock(side_effect=RuntimeError("Firestore unavailable"))
    monkeypatch.setattr("app.api.v1.routes.FirestoreSessionStore", constructor)
    try:
        with pytest.raises(RuntimeError, match="Firestore unavailable"):
            get_session_service()
    finally:
        get_session_service.cache_clear()
