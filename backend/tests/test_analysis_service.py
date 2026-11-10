"""外部課金なしでVertex AIの入出力・失敗処理を検証する。"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient
from google.genai import errors

from app.core.config import Settings, get_settings
from app.main import app
from app.schemas.analysis import AnalyzeRequest
from app.services.analysis_service import AnalysisError, AnalysisService


def request_payload():
    return AnalyzeRequest(
        sessionId="private-session", utterances=[
            {"id": "u1", "text": "銀行の暗証番号を教えてください", "startSec": 1},
        ],
    )


def response_payload(risk="high"):
    return {
        "is_suspicious": risk != "low", "risk_level": risk,
        "category": None if risk == "low" else "C2",
        "quote": None if risk == "low" else "暗証番号を教えてください",
        "reason": "認証情報の開示要求があります", "advice": "伝えず確認してください",
    }


@pytest.fixture
def vertex(monkeypatch):
    generate = AsyncMock()
    ai = AsyncMock()
    ai.__aenter__.return_value = ai
    ai.models = SimpleNamespace(generate_content=generate)
    factory = Mock(
        return_value=SimpleNamespace(aio=ai),
    )
    monkeypatch.setattr("app.services.analysis_service.genai.Client", factory)
    settings = Settings(
        _env_file=None, analysis_backend="vertexai", gcp_project_id="test-project",
        gcp_location="global", gemini_model="test-model", analysis_max_retries=0,
    )
    return settings, generate, factory


@pytest.mark.parametrize("risk", ["low", "medium", "high"])
def test_vertex_returns_validated_response(vertex, risk):
    settings, generate, factory = vertex
    generate.return_value = SimpleNamespace(text=json.dumps(response_payload(risk)))
    result = asyncio.run(AnalysisService(settings).analyze(request_payload()))
    assert result.model_dump() == response_payload(risk)
    assert factory.call_args.kwargs["vertexai"] is True
    assert factory.call_args.kwargs["project"] == "test-project"
    assert generate.call_args.kwargs["model"] == "test-model"
    assert generate.call_args.kwargs["config"].response_mime_type == "application/json"


def test_vertex_accepts_low_concern_with_quote(vertex):
    settings, generate, _ = vertex
    payload = {**response_payload("high"), "risk_level": "low"}
    generate.return_value = SimpleNamespace(text=json.dumps(payload))
    result = asyncio.run(AnalysisService(settings).analyze(request_payload()))
    assert result.is_suspicious is True
    assert result.risk_level == "low"
    assert result.quote == payload["quote"]


def test_only_latest_context_text_is_sent(vertex):
    settings, generate, _ = vertex
    settings.analysis_context_utterances = 2
    generate.return_value = SimpleNamespace(text=json.dumps(response_payload("low")))
    request = AnalyzeRequest(sessionId="private-session", utterances=[
        {"id": f"u{i}", "text": f"発言{i}", "startSec": i} for i in range(4)
    ])
    asyncio.run(AnalysisService(settings).analyze(request))
    sent = json.loads(generate.call_args.kwargs["contents"])
    assert sent == {"utterances": [{"text": "発言2"}, {"text": "発言3"}]}


@pytest.mark.parametrize("text", [
    "not JSON",
    json.dumps({**response_payload(), "quote": "存在しない引用"}),
    json.dumps({**response_payload(), "is_suspicious": False}),
    json.dumps({**response_payload(), "category": "C6"}),
])
def test_invalid_output_is_error_not_low(vertex, text):
    settings, generate, _ = vertex
    generate.return_value = SimpleNamespace(text=text)
    with pytest.raises(AnalysisError) as exc:
        asyncio.run(AnalysisService(settings).analyze(request_payload()))
    assert exc.value.status_code == 502
    assert "存在しない引用" not in exc.value.detail


def test_invalid_response_retries_once(vertex, monkeypatch):
    settings, generate, _ = vertex
    settings.analysis_max_retries = 1
    monkeypatch.setattr("app.services.analysis_service.asyncio.sleep", AsyncMock())
    generate.side_effect = [
        SimpleNamespace(text="bad JSON"),
        SimpleNamespace(text=json.dumps(response_payload())),
    ]
    result = asyncio.run(AnalysisService(settings).analyze(request_payload()))
    assert result.risk_level == "high"
    assert generate.await_count == 2


def test_timeout_returns_504(vertex):
    settings, generate, _ = vertex
    settings.analysis_timeout_seconds = 0.01

    async def slow(**kwargs):
        await asyncio.sleep(1)

    generate.side_effect = slow
    with pytest.raises(AnalysisError) as exc:
        asyncio.run(AnalysisService(settings).analyze(request_payload()))
    assert exc.value.status_code == 504


@pytest.mark.parametrize("code,expected_calls", [(403, 1), (429, 2), (503, 2)])
def test_upstream_errors_have_bounded_retries(vertex, monkeypatch, code, expected_calls):
    settings, generate, _ = vertex
    settings.analysis_max_retries = 1
    monkeypatch.setattr("app.services.analysis_service.asyncio.sleep", AsyncMock())
    generate.side_effect = errors.APIError(code, {"error": {"message": "private text"}})
    with pytest.raises(AnalysisError) as exc:
        asyncio.run(AnalysisService(settings).analyze(request_payload()))
    assert exc.value.status_code == 502
    assert "private text" not in exc.value.detail
    assert generate.await_count == expected_calls


def test_missing_configuration_does_not_call_ai(vertex):
    settings, generate, _ = vertex
    settings.gcp_project_id = ""
    with pytest.raises(AnalysisError) as exc:
        asyncio.run(AnalysisService(settings).analyze(request_payload()))
    assert exc.value.status_code == 503
    generate.assert_not_called()


def test_analysis_logs_do_not_include_input_or_output(vertex, caplog):
    settings, generate, _ = vertex
    generate.return_value = SimpleNamespace(text=json.dumps(response_payload()))
    with caplog.at_level("INFO", logger="app.services.analysis_service"):
        asyncio.run(AnalysisService(settings).analyze(request_payload()))
    assert "duration_ms=" in caplog.text
    assert "private-session" not in caplog.text
    assert "暗証番号" not in caplog.text


def test_api_maps_analysis_error(monkeypatch, tmp_path):
    monkeypatch.setenv("ANALYSIS_STATE_BACKEND", "sqlite")
    monkeypatch.setenv("ANALYSIS_STATE_PATH", str(tmp_path / "state.sqlite3"))
    monkeypatch.setenv("BACKEND_API_KEY", "test-key")
    get_settings.cache_clear()
    monkeypatch.setattr(
        AnalysisService, "analyze",
        AsyncMock(side_effect=AnalysisError(504, "AI analysis timed out")),
    )
    try:
        with TestClient(app) as client:
            response = client.post(
                "/analyze", headers={"X-API-Key": "test-key"},
                json={"sessionId": "api-error", "utterances": [
                    {"id": f"u{i}", "text": "発言", "startSec": i} for i in range(6)
                ]},
            )
        assert response.status_code == 504
        assert response.json() == {"detail": "AI analysis timed out"}
    finally:
        get_settings.cache_clear()


def test_quote_from_excluded_context_is_rejected(vertex):
    settings, generate, _ = vertex
    settings.analysis_context_utterances = 1
    generate.return_value = SimpleNamespace(text=json.dumps(response_payload()))
    request = request_payload()
    request.utterances.append(type(request.utterances[0])(
        id="u2", text="熱はいつからですか", startSec=2,
    ))
    with pytest.raises(AnalysisError) as exc:
        asyncio.run(AnalysisService(settings).analyze(request))
    assert exc.value.status_code == 502


def test_google_cloud_environment_aliases(monkeypatch):
    monkeypatch.delenv("GCP_PROJECT_ID", raising=False)
    monkeypatch.delenv("GCP_LOCATION", raising=False)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "alias-project")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "global")
    settings = Settings(_env_file=None)
    assert settings.gcp_project_id == "alias-project"
    assert settings.gcp_location == "global"
    monkeypatch.setenv("GCP_PROJECT_ID", "preferred-project")
    monkeypatch.setenv("GCP_LOCATION", "asia-northeast1")
    settings = Settings(_env_file=None)
    assert settings.gcp_project_id == "preferred-project"
    assert settings.gcp_location == "asia-northeast1"


def test_usage_metadata_is_recorded_without_response_text(vertex, caplog):
    from google.genai import types

    settings, generate, _ = vertex
    generate.return_value = SimpleNamespace(
        text=json.dumps(response_payload()),
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=100, candidates_token_count=30,
            thoughts_token_count=20, total_token_count=150,
        ),
    )
    with caplog.at_level("INFO", logger="app.services.analysis_service"):
        asyncio.run(AnalysisService(settings).analyze(request_payload()))
    usage = [r.analysis_usage for r in caplog.records if hasattr(r, "analysis_usage")]
    assert len(usage) == 1
    assert usage[0]["usage_metadata"]["prompt_token_count"] == 100
    assert usage[0]["usage_metadata"]["thoughts_token_count"] == 20
    assert "暗証番号" not in json.dumps(usage, ensure_ascii=False)
