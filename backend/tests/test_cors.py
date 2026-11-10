"""CORSはブラウザの応答読み取りを制御し、API認証とは別に扱う。"""

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("ANALYSIS_STATE_BACKEND", "sqlite")
    monkeypatch.setenv("ANALYSIS_STATE_PATH", str(tmp_path / "state.sqlite3"))
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", "http://localhost:3000")
    monkeypatch.setenv("ANALYSIS_BACKEND", "mock")
    monkeypatch.setenv("BACKEND_API_KEY", "cors-test-key")
    get_settings.cache_clear()
    with TestClient(create_app()) as test_client:
        yield test_client
    get_settings.cache_clear()


def test_allowed_preflight_without_api_key(client):
    res = client.options("/analyze", headers={
        "Origin": "http://localhost:3000",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type,x-api-key",
    })
    assert res.status_code == 200
    assert res.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert "x-api-key" in res.headers["access-control-allow-headers"].lower()


@pytest.mark.parametrize("origin", ["https://untrusted.example", "https://meet.google.com"])
def test_unlisted_origin_preflight_is_rejected(client, origin):
    res = client.options("/analyze", headers={
        "Origin": origin,
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "x-api-key",
    })
    assert res.status_code == 400
    assert "access-control-allow-origin" not in res.headers


def test_authentication_error_is_readable_by_allowed_origin(client):
    res = client.post("/analyze", headers={"Origin": "http://localhost:3000"}, json={})
    assert res.status_code == 401
    assert res.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_analyze_accepts_no_origin_with_valid_key(client):
    assert client.post("/api/v1/sessions", json={"session_id": "s"}).status_code == 201
    res = client.post("/analyze", headers={"X-API-Key": "cors-test-key"}, json={
        "sessionId": "s", "utterances": [
            {"id": f"u{i}",
             "text": "今すぐこの口座に振り込んでください" if i == 5 else "確認",
             "startSec": i} for i in range(6)
        ],
    })
    assert res.status_code == 200
