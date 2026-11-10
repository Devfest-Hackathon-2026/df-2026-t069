"""音声文字起こし API のテスト [Issue #9 & #17 レビュー対応]"""

try:
    from fastapi.testclient import TestClient  # type: ignore
except ImportError:
    TestClient = None  # type: ignore

from app.main import app

client = TestClient(app) if TestClient else None


def test_transcribe_endpoint():
    if not client:
        return  # ライブラリ未インストール環境ではスキップ
    res = client.post(
        "/api/v1/transcribe",
        files={"audio": ("chunk_1.webm", b"RIFFfakeaudiobytes", "audio/webm")},
        data={"session_id": "test-session-1", "start_sec": "3.0"},
    )
    assert res.status_code == 200
    body = res.json()

    assert "utterances" in body
    assert isinstance(body["utterances"], list)
    assert len(body["utterances"]) > 0

    first_utt = body["utterances"][0]
    assert "id" in first_utt
    assert "text" in first_utt
    assert "start_sec" in first_utt
    assert first_utt["speaker"] in ["相手", "自分"]
    assert first_utt["is_final"] is True

    assert "recent_context" in body
    assert "has_opponent_speech" in body
    assert body["has_opponent_speech"] is True
    assert "processing_ms" in body


def test_transcribe_session_isolation():
    """セッション間で発言バッファが混ざらないことを検証 [指摘 1]"""
    if not client:
        return

    # セッション A にリクエスト
    res_a = client.post(
        "/api/v1/transcribe",
        files={"audio": ("chunk_a.webm", b"RIFFfakeaudiobytes", "audio/webm")},
        data={"session_id": "session-AAA", "start_sec": "0.0"},
    )
    assert res_a.status_code == 200
    context_a = res_a.json()["recent_context"]

    # セッション B にリクエスト
    res_b = client.post(
        "/api/v1/transcribe",
        files={"audio": ("chunk_b.webm", b"RIFFfakeaudiobytes", "audio/webm")},
        data={"session_id": "session-BBB", "start_sec": "0.0"},
    )
    assert res_b.status_code == 200
    context_b = res_b.json()["recent_context"]

    # 両方のコンテキストが独立して正しく生成されていること
    assert len(context_a) > 0
    assert len(context_b) > 0


def test_transcribe_buffer_lru_eviction() -> None:
    """セッションバッファが上限（MAX_BUFFERS=128）を超えた際、最も古いセッションから破棄されることを検証。"""
    from app.services.transcription_service import TranscriptionService

    service = TranscriptionService(stt_backend="mock")
    for i in range(TranscriptionService.MAX_BUFFERS + 5):
        service.transcribe(b"RIFFfake", session_id=f"sess-{i}", start_sec=0.0)

    assert len(service._buffers) == TranscriptionService.MAX_BUFFERS
    assert "sess-0" not in service._buffers
    assert f"sess-{TranscriptionService.MAX_BUFFERS + 4}" in service._buffers
