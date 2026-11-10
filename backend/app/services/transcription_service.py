"""音声文字起こしおよび発言バッファリングサービス [Issue #9]

役割:
 - #5 で選定した Google Cloud Speech-to-Text v2（またはモック）を用いてステレオ音声を文字起こし
 - 日本語の文区切り（「。」「！」「？」）の正規化
 - セッションごとの独立した「直近5文」文脈バッファリング
 - WebM/Opus コンテナの自動デコード対応
"""

from __future__ import annotations

import collections
import logging
import os
import re
import time
import uuid

from app.core.config import get_settings
from app.schemas.transcription import TranscriptionResponse, Utterance

logger = logging.getLogger(__name__)


class SentenceSplitter:
    """日本語テキストを文単位に区切るユーティリティクラス。"""

    _DELIMITER_PATTERN = re.compile(r"([。！？\?\!\n]+)")

    @classmethod
    def split_sentences(cls, text: str) -> list[str]:
        """文末記号で分割し、意味のある文のリストを返す。"""
        if not text:
            return []

        tokens = cls._DELIMITER_PATTERN.split(text.strip())
        sentences: list[str] = []
        current = ""

        for token in tokens:
            if not token:
                continue
            current += token
            if cls._DELIMITER_PATTERN.match(token):
                cleaned = current.strip()
                if cleaned:
                    sentences.append(cleaned)
                current = ""

        if current.strip():
            sentences.append(current.strip())

        return sentences


class UtteranceBuffer:
    """直近の発言を保持するFIFOリングバッファ。"""

    def __init__(self, max_size: int = 5) -> None:
        self._max_size = max_size
        self._buffer: collections.deque[Utterance] = collections.deque(maxlen=max_size)

    def add(self, utterance: Utterance) -> None:
        self._buffer.append(utterance)

    def get_context_text(self) -> str:
        """直近の文脈テキストを生成する。"""
        return "\n".join(f"[{u.speaker}] {u.text}" for u in self._buffer)


class TranscriptionService:
    """セッション単位の文脈バッファを持つ音声文字起こしオーケストレータ。"""

    MAX_BUFFERS = 128

    def __init__(
        self,
        project_id: str | None = None,
        stt_backend: str | None = None,
    ) -> None:
        settings = get_settings()
        self.project_id = project_id or settings.gcp_project_id or os.environ.get(
            "GCP_PROJECT_ID", ""
        )
        self.stt_backend = stt_backend or settings.stt_backend
        # セッションIDごとに独立した発言バッファをLRU管理（セッション間混在とメモリ肥大化を防止）
        self._buffers: collections.OrderedDict[str, UtteranceBuffer] = (
            collections.OrderedDict()
        )

    def _get_buffer(self, session_id: str) -> UtteranceBuffer:
        if session_id in self._buffers:
            self._buffers.move_to_end(session_id)
            return self._buffers[session_id]

        self._buffers[session_id] = UtteranceBuffer(max_size=5)
        while len(self._buffers) > self.MAX_BUFFERS:
            self._buffers.popitem(last=False)
        return self._buffers[session_id]

    def transcribe(
        self,
        audio_data: bytes,
        session_id: str = "default",
        start_sec: float = 0.0,
    ) -> TranscriptionResponse:
        started_at = time.perf_counter()

        raw_results = self._call_stt(audio_data)

        buf = self._get_buffer(session_id)
        utterances: list[Utterance] = []
        for channel, text in raw_results:
            speaker = "自分" if channel == 1 else "相手"
            sentences = SentenceSplitter.split_sentences(text)

            for i, sent in enumerate(sentences):
                # チャンク内の文ごとに微小な秒数を加算
                approx_sec = round(start_sec + (i * 1.5), 1)
                utt = Utterance(
                    id=f"utt_{uuid.uuid4().hex[:8]}",
                    text=sent,
                    start_sec=approx_sec,
                    speaker=speaker,
                    is_final=True,
                )
                utterances.append(utt)
                buf.add(utt)

        has_opponent = any(u.speaker == "相手" for u in utterances)
        elapsed_ms = int((time.perf_counter() - started_at) * 1000)

        return TranscriptionResponse(
            utterances=utterances,
            recent_context=buf.get_context_text(),
            has_opponent_speech=has_opponent,
            processing_ms=elapsed_ms,
        )

    def _call_stt(self, audio_data: bytes) -> list[tuple[int, str]]:
        """GCP API またはモックを呼び出す。モックと本番を明確に分離。"""
        if self.stt_backend == "gcp":
            # 本番環境: 失敗時はモックへフォールバックせず、明示的にエラーを発生させて誤検知を防ぐ
            return self._call_real_gcp_stt(audio_data)

        # モックモード (stt_backend == "mock")
        return [
            (2, "担当医の山田です。システムの不具合でお薬の配送に手数料が必要です。"),
            (2, "至急指定口座へお振込みいただけますでしょうか？"),
        ]

    def _call_real_gcp_stt(self, audio_data: bytes) -> list[tuple[int, str]]:
        """Google Cloud Speech-to-Text v2 API を呼び出す（WebM/Opus自動デコード）。"""
        try:
            from google.cloud import speech_v2 as speech  # type: ignore

            client = speech.SpeechClient()
            # auto_decoding_config で WebM/Opus などのコンテナヘッダーを自動認識
            config = speech.RecognitionConfig(
                auto_decoding_config=speech.AutoDetectDecodingConfig(),
                features=speech.RecognitionFeatures(
                    multi_channel_mode=speech.RecognitionFeatures.MultiChannelMode.SEPARATE_RECOGNITION_PER_CHANNEL,
                ),
                language_codes=["ja-JP"],
                model="long",
            )
            recognizer = f"projects/{self.project_id}/locations/global/recognizers/_"
            request = speech.RecognizeRequest(
                recognizer=recognizer,
                config=config,
                content=audio_data,
            )
            response = client.recognize(request=request)

            results: list[tuple[int, str]] = []
            for r in response.results:
                if r.alternatives:
                    results.append((r.channel_tag or 1, r.alternatives[0].transcript.strip()))
            return results
        except Exception as e:
            logger.error("GCP STT API 呼び出しに失敗しました: %s", e)
            raise RuntimeError(f"GCP STT recognition failed: {e}") from e
