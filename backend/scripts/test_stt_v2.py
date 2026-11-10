#!/usr/bin/env python3
"""Google Cloud Speech-to-Text v2 マルチチャンネル（ステレオ）文字起こし検証スクリプト。

Issue #5 の検証用:
- #4 でステレオ録音された音声（L=自分, R=相手）を左右別々に文字起こしする。
- channel_tag: 1 (自分), channel_tag: 2 (相手) が明確に分離されることを確認する。
- GCP認証がない環境でも `--mock` フラグで安全に動作シミュレーションが可能。
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from dataclasses import dataclass
from typing import Protocol

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class TranscriptUtterance:
    """発言単位の文字起こしデータ。"""

    channel_tag: int
    speaker_role: str  # "自分 (L)" または "相手 (R)"
    transcript: str

    def __str__(self) -> str:
        return f"[{self.speaker_role}] {self.transcript}"


class Transcriber(Protocol):
    """文字起こし器のインターフェース。"""

    def transcribe(self, audio_content: bytes) -> list[TranscriptUtterance]: ...


class CloudSpeechV2Transcriber:
    """Google Cloud Speech-to-Text v2 クライアント実装。"""

    def __init__(self, project_id: str, location: str = "global") -> None:
        self.project_id = project_id
        self.location = location

        try:
            # 型チェッカーの未インストール警告を抑制
            from google.cloud import speech_v2 as speech  # type: ignore

            self._speech = speech
            self._client = speech.SpeechClient()
        except ImportError as exc:
            raise RuntimeError(
                "google-cloud-speech がインストールされていません。\n"
                "実行するには: pip install google-cloud-speech"
            ) from exc

    def transcribe(self, audio_content: bytes) -> list[TranscriptUtterance]:
        """ステレオ音声バイナリを受け取り、チャンネルごとに文字起こしを実行する。"""
        speech = self._speech

        config = speech.RecognitionConfig(
            explicit_decoding_config=speech.ExplicitDecodingConfig(
                encoding=speech.ExplicitDecodingConfig.AudioEncoding.LINEAR16,
                sample_rate_hertz=16000,
                audio_channel_count=2,  # ステレオ (2ch)
            ),
            # ★核心設定: チャンネルごとに独立して文字起こしを行い channel_tag を付与
            enable_separate_recognition_per_channel=True,
            language_codes=["ja-JP"],
            model="long",
            adaptation=speech.SpeechAdaptation(
                phrase_sets=[
                    speech.SpeechAdaptation.AdaptationPhraseSet(
                        inline_phrase_set=speech.PhraseSet(
                            phrases=[
                                speech.PhraseSet.Phrase(value="オンライン診療", boost=15.0),
                                speech.PhraseSet.Phrase(value="処方箋", boost=15.0),
                                speech.PhraseSet.Phrase(value="保険証番号", boost=15.0),
                                speech.PhraseSet.Phrase(value="至急お振込み", boost=15.0),
                                speech.PhraseSet.Phrase(value="生年月日", boost=10.0),
                            ]
                        )
                    )
                ]
            ),
        )

        recognizer_path = f"projects/{self.project_id}/locations/{self.location}/recognizers/_"
        request = speech.RecognizeRequest(
            recognizer=recognizer_path,
            config=config,
            content=audio_content,
        )

        logger.info("Speech-to-Text v2 API を呼び出しています (recognizer=%s)...", recognizer_path)
        response = self._client.recognize(request=request)

        results: list[TranscriptUtterance] = []
        for result in response.results:
            if not result.alternatives:
                continue
            channel = result.channel_tag or 1
            speaker = "自分 (L)" if channel == 1 else "相手 (R)"
            text = result.alternatives[0].transcript.strip()
            if text:
                results.append(
                    TranscriptUtterance(
                        channel_tag=channel,
                        speaker_role=speaker,
                        transcript=text,
                    )
                )

        return results


class MockStereoTranscriber:
    """GCP環境がないローカル検証用のモック文字起こし器。"""

    def transcribe(self, audio_content: bytes) -> list[TranscriptUtterance]:
        logger.info("モック文字起こし器を実行中 (音声バイト数: %d bytes)...", len(audio_content))
        return [
            TranscriptUtterance(
                channel_tag=1,
                speaker_role="自分 (L)",
                transcript="こんにちは、本日オンライン診療を予約した患者の佐藤です。",
            ),
            TranscriptUtterance(
                channel_tag=2,
                speaker_role="相手 (R)",
                transcript="担当医の山田です。システムの不具合でお薬の配送手続きに別途手数料が必要です。今すぐ指定口座にお振込みいただけますか？",
            ),
            TranscriptUtterance(
                channel_tag=1,
                speaker_role="自分 (L)",
                transcript="えっ、診察前に振込ですか？保険証の提示は必要ないのですか？",
            ),
            TranscriptUtterance(
                channel_tag=2,
                speaker_role="相手 (R)",
                transcript="保険証番号と生年月日は後で構いません。至急お振込みください。",
            ),
        ]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Speech-to-Text v2 マルチチャンネル検証")
    parser.add_argument(
        "--file",
        "-f",
        type=str,
        help="検証用のステレオ音声ファイルパス (*.wav, *.webm)",
    )
    parser.add_argument(
        "--mock",
        "-m",
        action="store_true",
        help="GCP APIを呼ばず、モックデータでパイプライン動作を検証する",
    )
    parser.add_argument(
        "--project-id",
        type=str,
        default=os.environ.get("GCP_PROJECT_ID", ""),
        help="Google Cloud プロジェクトID (環境変数 GCP_PROJECT_ID でも指定可)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    audio_bytes = b""
    if args.file:
        if not os.path.exists(args.file):
            logger.error("指定されたファイルが存在しません: %s", args.file)
            return 1
        with open(args.file, "rb") as f:
            audio_bytes = f.read()
        logger.info("音声ファイルを読み込みました: %s (%d bytes)", args.file, len(audio_bytes))
    else:
        audio_bytes = b"MOCK_STEREO_AUDIO_CONTENT"

    transcriber: Transcriber
    if args.mock or not args.project_id:
        if not args.mock and not args.project_id:
            logger.warning("GCP_PROJECT_ID が指定されていないため、モックモードで実行します。")
        transcriber = MockStereoTranscriber()
    else:
        transcriber = CloudSpeechV2Transcriber(project_id=args.project_id)

    print("\n========== 文字起こし結果 (チャンネル別) ==========")
    utterances = transcriber.transcribe(audio_bytes)
    for u in utterances:
        print(f"[{u.channel_tag}] {u.speaker_role}: {u.transcript}")
    print("===================================================\n")

    # 相手の発言のみを抽出するフィルタリングテスト (#11連携)
    opponent_speech = [u.transcript for u in utterances if u.channel_tag == 2]
    print(f"★ Gemini 不審判定 (#11) へ渡す相手の発言 ({len(opponent_speech)} 件):")
    for text in opponent_speech:
        print(f"  - 「{text}」")

    return 0


if __name__ == "__main__":
    sys.exit(main())

