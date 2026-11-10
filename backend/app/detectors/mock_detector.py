"""APIキー無しで全フローを動かすためのモック検知器。[Role3]

- UI開発（Role1/Role2）を Gemini 実装の完成を待たずに進められる
- 会場のネットワーク不調時のデモ用フォールバック
- 同じファイルには常に同じ結果を返す（デモの再現性）
"""

import hashlib

from app.detectors.base import Detector, MediaInput
from app.schemas.detection import DetectionResult, Evidence, Verdict


class MockDetector(Detector):
    @property
    def name(self) -> str:
        return "mock"

    async def analyze(self, media: MediaInput) -> DetectionResult:
        digest = hashlib.sha256(media.data).digest()
        score = round(digest[0] / 255, 2)

        if score >= 0.7:
            verdict = Verdict.LIKELY_FAKE
            summary = "合成音声・映像の特徴が複数検出されました（モック結果）。"
            actions = ["一度通話を切り、登録済みの本人の番号にかけ直してください。"]
        elif score >= 0.4:
            verdict = Verdict.SUSPICIOUS
            summary = "一部に不自然な特徴があります（モック結果）。"
            actions = ["本人しか知らない質問で確認してください。"]
        else:
            verdict = Verdict.AUTHENTIC
            summary = "目立った合成の特徴は見つかりませんでした（モック結果）。"
            actions = []

        return DetectionResult(
            verdict=verdict,
            risk_score=score,
            summary=summary,
            evidences=[
                Evidence(
                    category=f"{media.modality}_mock",
                    description="これはモック検知器による固定的な説明です。",
                    severity=score,
                )
            ],
            recommended_actions=actions,
        )
