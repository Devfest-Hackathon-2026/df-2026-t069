"""API の E2E テスト（MockDetector 使用・APIキー不要）。"""

import pytest
from fastapi.testclient import TestClient

from app.api.v1.routes import get_session_service
from app.core.config import get_settings
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _force_mock(monkeypatch, tmp_path):
    monkeypatch.setenv("GCP_PROJECT_ID", "")
    get_session_service.cache_clear()
    monkeypatch.setenv("ANALYSIS_STATE_BACKEND", "sqlite")
    monkeypatch.setenv("ANALYSIS_STATE_PATH", str(tmp_path / "state.sqlite3"))
    monkeypatch.setenv("DETECTOR_BACKEND", "mock")
    monkeypatch.setenv("ANALYSIS_BACKEND", "mock")
    monkeypatch.setenv("BACKEND_API_KEY", "test-backend-key")
    get_settings.cache_clear()


def test_health():
    res = client.get("/api/v1/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


def test_healthz_without_gemini_credentials(monkeypatch):
    monkeypatch.setenv("DETECTOR_BACKEND", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("GEMINI_USE_VERTEXAI", "false")
    get_settings.cache_clear()

    res = client.get("/healthz")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


def test_detect_returns_contract_shape():
    res = client.post(
        "/api/v1/detect",
        files={"media": ("sample.wav", b"RIFF....WAVEfmt fake-bytes", "audio/wav")},
        data={"context": "息子を名乗る電話"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["verdict"] in {"authentic", "suspicious", "likely_fake", "unknown"}
    assert 0.0 <= body["risk_score"] <= 1.0
    assert body["detector"] == "mock"
    assert isinstance(body["evidences"], list)


@pytest.mark.parametrize("count", [6, 8])
def test_analyze_accepts_variable_utterance_count(count):
    assert client.post(
        "/api/v1/sessions", json={"session_id": "session-001"},
    ).status_code == 201
    res = client.post("/analyze", headers={"X-API-Key": "test-backend-key"}, json={
        "sessionId": "session-001",
        "utterances": [
            {"id": f"u-{i}",
             "text": "今すぐこの口座に振り込んでください" if i == count - 1 else "通常の会話",
             "startSec": i * 1.5}
            for i in range(count)
        ],
    })
    assert res.status_code == 200
    assert res.json() == {
        "is_suspicious": True,
        "risk_level": "high",
        "category": "C1",
        "quote": "今すぐこの口座に振り込んでください",
        "reason": "【モック】送金を急がせる発言を検出した場合の判定例です。",
        "advice": "その場で振り込まず、公式窓口に確認してください。",
    }


@pytest.mark.parametrize("payload", [
    {},
    {"sessionId": "s", "utterances": []},
    {"sessionId": "s", "utterances": [{"id": "u", "text": "発言", "startSec": -1}]},
    {"sessionId": "s", "utterances": [{"id": "u", "text": "", "startSec": 0}]},
])
def test_analyze_rejects_invalid_request(payload):
    res = client.post(
        "/analyze", headers={"X-API-Key": "test-backend-key"}, json=payload
    )
    assert res.status_code == 422


@pytest.mark.parametrize("headers", [{}, {"X-API-Key": "wrong-key"}, {"X-API-Key": ""}])
def test_analyze_rejects_missing_or_invalid_api_key(headers):
    res = client.post("/analyze", headers=headers, json={
        "sessionId": "s",
        "utterances": [{"id": "u", "text": "発言", "startSec": 0}],
    })
    assert res.status_code == 401
    assert res.json() == {"detail": "Invalid or missing API key"}


@pytest.mark.parametrize("configured_key", ["", "   "])
def test_analyze_fails_closed_without_server_key(monkeypatch, configured_key):
    monkeypatch.setenv("BACKEND_API_KEY", configured_key)
    get_settings.cache_clear()
    res = client.post("/analyze", headers={"X-API-Key": "test-backend-key"}, json={
        "sessionId": "s",
        "utterances": [{"id": "u", "text": "発言", "startSec": 0}],
    })
    assert res.status_code == 503
    assert res.json() == {"detail": "API authentication is not configured"}
    assert client.get("/healthz").status_code == 200


def test_openapi_declares_analyze_api_key_auth():
    schema = client.get("/openapi.json").json()
    assert schema["paths"]["/analyze"]["post"]["security"] == [{"APIKeyHeader": []}]
    assert schema["components"]["securitySchemes"]["APIKeyHeader"]["name"] == "X-API-Key"


def test_detect_is_deterministic_for_same_input():
    files = {"media": ("a.png", b"same-bytes", "image/png")}
    first = client.post("/api/v1/detect", files=files).json()
    second = client.post("/api/v1/detect", files=files).json()
    assert first["risk_score"] == second["risk_score"]


def test_rejects_unsupported_mime():
    res = client.post(
        "/api/v1/detect", files={"media": ("evil.sh", b"#!/bin/sh", "application/x-sh")}
    )
    assert res.status_code == 415


def test_rejects_empty_file():
    res = client.post("/api/v1/detect", files={"media": ("e.wav", b"", "audio/wav")})
    assert res.status_code == 400


def test_rejects_too_large_file(monkeypatch):
    monkeypatch.setenv("MAX_UPLOAD_BYTES", "10")
    get_settings.cache_clear()
    res = client.post("/api/v1/detect", files={"media": ("big.wav", b"x" * 11, "audio/wav")})
    assert res.status_code == 413
