"""実Emulatorで検証。ローカルホスト指定時のみ実行し、本番・Geminiへ接続しない。"""

import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import AsyncMock, Mock
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.v1.routes import get_session_service
from app.core.config import get_settings
from app.main import create_app
from app.schemas.analysis import AnalyzeResponse
from app.schemas.session import DetectionItem
from app.services.analysis_service import AnalysisError
from app.services.analysis_window_store import _digest
from app.services.firestore_analysis_store import COLLECTION, FirestoreAnalysisWindowStore

HOST = os.environ.get("FIRESTORE_EMULATOR_HOST", "")
pytestmark = pytest.mark.skipif(not HOST, reason="Firestore Emulator is not configured")


@pytest.fixture
def emulator(monkeypatch):
    host = urlsplit(f"//{HOST}")
    if host.hostname not in {"localhost", "127.0.0.1", "::1"} or not host.port:
        pytest.fail("FIRESTORE_EMULATOR_HOST must be an explicit loopback host:port")
    monkeypatch.setenv("GCP_PROJECT_ID", "demo-waiwai-emulator")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "demo-waiwai-emulator")
    monkeypatch.setenv("ANALYSIS_BACKEND", "mock")
    monkeypatch.setenv("ANALYSIS_STATE_BACKEND", "firestore")
    monkeypatch.setenv("ANALYSIS_FIRESTORE_DATABASE", "(default)")
    monkeypatch.setenv("ANALYSIS_CONTEXT_UTTERANCES", "5")
    monkeypatch.setenv("BACKEND_API_KEY", "emulator-test-key")
    # 万一コードがGeminiを呼ぼうとしても実リクエストの前に失敗させる。
    monkeypatch.setattr("app.services.analysis_service.genai.Client", Mock(
        side_effect=AssertionError("Gemini must not be called in emulator tests"),
    ))
    get_settings.cache_clear()
    get_session_service.cache_clear()
    yield FirestoreAnalysisWindowStore("demo-waiwai-emulator")
    get_session_service.cache_clear()
    get_settings.cache_clear()


def batch(session, start, count=5):
    return {"sessionId": session, "utterances": [
        {"id": f"u{i}",
         "text": "今すぐこの口座に振り込んでください" if i == start else "合成テストの発言",
         "startSec": i}
        for i in range(start, start + count)
    ]}


def test_http_three_batches_and_duplicate_ids(emulator):
    session = uuid4().hex
    with TestClient(create_app(), headers={"X-API-Key": "emulator-test-key"}) as client:
        assert client.post("/api/v1/sessions", json={"session_id": session}).status_code == 201
        assert client.post("/analyze", json=batch(session, 1, 4)).status_code == 204
        for start in (1, 6, 11):
            assert client.post("/analyze", json=batch(session, start)).status_code == 200
            response = client.post("/analyze", json=batch(session, start))
            assert response.status_code == 204
            assert response.content == b""
        assert client.post("/analyze", json=batch(session, 12)).status_code == 204
        report = client.get(f"/api/v1/sessions/{session}").json()
        assert report["detections_count"] == 3
        assert report["max_risk_score"] == 0.85
        assert [d["timestamp_sec"] for d in report["detections"]] == [1, 6, 11]
    data = emulator.client.collection(COLLECTION).document(_digest(session)).get().to_dict()
    assert len(data["completed_ids"]) == 15
    assert data["pending"] is None
    assert "合成テスト" not in str(data)


def test_shared_client_reservation_and_session_isolation(emulator):
    session = uuid4().hex
    second = FirestoreAnalysisWindowStore("demo-waiwai-emulator")
    token = emulator.claim(session, ["u1", "u2", "u3", "u4", "u5"], 60)
    assert token
    assert second.claim(session, ["u1", "u2", "u3", "u4", "u5"], 60) is None
    emulator.finish(token, True)
    assert second.claim(session, ["u1", "u2", "u3", "u4", "u5"], 60) is None
    other = second.claim(session + "-other", ["u1", "u2", "u3", "u4", "u5"], 60)
    assert other
    second.finish(other, False)


def test_http_failure_releases_reservation(emulator, monkeypatch):
    session = uuid4().hex
    with TestClient(create_app(), headers={"X-API-Key": "emulator-test-key"}) as client:
        assert client.post("/api/v1/sessions", json={"session_id": session}).status_code == 201
        with monkeypatch.context() as patch:
            patch.setattr("app.api.analyze.AnalysisService.analyze", AsyncMock(
                side_effect=AnalysisError(504, "AI analysis timed out"),
            ))
            assert client.post("/analyze", json=batch(session, 1)).status_code == 504
        assert client.post("/analyze", json=batch(session, 1)).status_code == 200
        assert client.post("/analyze", json=batch(session, 1)).status_code == 204


def test_real_transaction_concurrent_claim_has_one_owner(emulator):
    session = uuid4().hex
    barrier = Barrier(2)

    def claim(_):
        store = FirestoreAnalysisWindowStore("demo-waiwai-emulator")
        barrier.wait(timeout=5)
        return store.claim(session, ["u1", "u2", "u3", "u4", "u5"], 60)

    with ThreadPoolExecutor(max_workers=2) as pool:
        owners = list(pool.map(claim, range(2)))
    assert sum(token is not None for token in owners) == 1
    emulator.finish(next(token for token in owners if token), False)


def test_detection_retry_and_concurrent_writes_keep_report_consistent(emulator):
    service = get_session_service()
    session = uuid4().hex
    service.create_session(session)
    item = DetectionItem(
        id="same-analysis", timestamp_sec=12.5, quote="合成の発言", category="C1",
        risk_score=0.85, risk_level="likely_fake", reason="合成の理由",
    )
    service.record_detection(session, item)
    service.record_detection(session, item)
    barrier = Barrier(2)

    def save(index):
        barrier.wait(timeout=5)
        return service.record_detection(session, item.model_copy(update={"id": f"other-{index}"}))

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(save, range(2)))
    report = service.get_session(session)
    assert report.detections_count == len(report.detections) == 3
    assert report.max_risk_score == 0.85
    assert report.overall_verdict == "likely_fake"
    assert all(d.expire_at == report.expire_at for d in report.detections)


def test_low_concern_is_saved_without_downgrading_high(emulator, monkeypatch):
    session = uuid4().hex
    analysis = AsyncMock(return_value=AnalyzeResponse(
        is_suspicious=True, risk_level="low", category="C1",
        quote="今すぐこの口座に振り込んでください", reason="合成データの軽い注意",
        advice="手順を確認してください",
    ))
    monkeypatch.setattr("app.api.analyze.AnalysisService.analyze", analysis)
    with TestClient(create_app(), headers={"X-API-Key": "emulator-test-key"}) as client:
        assert client.post("/api/v1/sessions", json={"session_id": session}).status_code == 201
        assert client.post("/analyze", json=batch(session, 1)).status_code == 200
        assert client.post("/analyze", json=batch(session, 1)).status_code == 204
        report = client.get(f"/api/v1/sessions/{session}").json()
        assert report["overall_verdict"] == "low"
        assert report["max_risk_score"] == 0.25
        assert report["detections_count"] == 1
        assert report["detections"][0]["risk_level"] == "low"
        assert report["detections"][0]["timestamp_sec"] == 1
        assert report["detections"][0]["expire_at"] == report["expire_at"]

        analysis.return_value.risk_level = "high"
        assert client.post("/analyze", json=batch(session, 6)).status_code == 200
        analysis.return_value.risk_level = "low"
        assert client.post("/analyze", json=batch(session, 11)).status_code == 200
        report = client.get(f"/api/v1/sessions/{session}").json()
        assert report["detections_count"] == 3
        assert report["overall_verdict"] == "likely_fake"
        assert report["max_risk_score"] == 0.85


def test_incomplete_report_survives_session_service_recreation(emulator):
    session = uuid4().hex
    with TestClient(create_app()) as client:
        assert client.post("/api/v1/sessions", json={"session_id": session}).status_code == 201
        url = f"/api/v1/sessions/{session}"
        assert client.post(url + "/end", json={"analysis_incomplete": True}).status_code == 200
        get_session_service.cache_clear()
        assert client.get(url).json()["analysis_incomplete"] is True
        assert client.post(url + "/end", json={}).status_code == 200
        assert client.get(url).json()["analysis_incomplete"] is True
