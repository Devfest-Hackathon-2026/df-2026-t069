"""API契約（フロントエンド frontend/src/types/detection.ts と1対1で対応）。

⚠ 変更時は docs/api-contract.md と TS 型も同時に更新し、Role2・Role3 双方のレビューを受けること。
"""

from enum import StrEnum

from pydantic import BaseModel, Field


class Verdict(StrEnum):
    AUTHENTIC = "authentic"  # 本物の可能性が高い
    SUSPICIOUS = "suspicious"  # 要注意（追加確認を推奨）
    LIKELY_FAKE = "likely_fake"  # なりすましの可能性が高い
    UNKNOWN = "unknown"  # 判定不能（品質不足など）


class Evidence(BaseModel):
    """判定根拠。UIで「なぜ怪しいのか」を説明するために使う。"""

    category: str = Field(description="例: voice_prosody, lip_sync, visual_artifact, context")
    description: str = Field(description="ユーザー向けの平易な説明（日本語）")
    severity: float = Field(ge=0.0, le=1.0)


class DetectionResult(BaseModel):
    verdict: Verdict
    risk_score: float = Field(ge=0.0, le=1.0, description="0=本物らしい, 1=偽物らしい")
    summary: str = Field(description="1〜2文の要約（日本語）")
    evidences: list[Evidence] = Field(default_factory=list)
    recommended_actions: list[str] = Field(
        default_factory=list, description="例: 一度電話を切って本人の番号にかけ直す"
    )


class DetectionResponse(DetectionResult):
    detector: str = Field(description="使用した検知器名 (gemini / mock)")
    model: str | None = None
    processing_ms: int
