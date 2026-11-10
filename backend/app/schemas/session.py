"""セッションおよび検知結果保存用スキーマ。[Role2]

Issue #17: 通話ごとの分析結果をFirestoreに保存し、通話後レポート(#18, #24, #25)で参照する。
※ 音声・動画などのメディアデータは一切保存しない（プライバシー保護方針 #6）。
"""

import uuid
from datetime import datetime, timezone
from enum import StrEnum

from pydantic import BaseModel, Field  # type: ignore


class SessionVerdict(StrEnum):
    """通話レポートの判定。メディア単発判定とは別に軽い注意lowを持つ。"""

    AUTHENTIC = "authentic"
    LOW = "low"
    SUSPICIOUS = "suspicious"
    LIKELY_FAKE = "likely_fake"
    UNKNOWN = "unknown"


class DetectionItem(BaseModel):
    """通話中に検知された不審箇所・なりすまし疑いレコード。"""

    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="検知レコードの一意ID",
    )
    timestamp_sec: float = Field(
        ge=0.0,
        description="通話開始からの経過秒数",
    )
    speaker: str | None = Field(
        default=None,
        description="話者（例: 'remote', 'local', またはチャンネル名）",
    )
    quote: str = Field(
        min_length=1,
        max_length=1000,
        description="検知対象となった発言テキスト",
    )
    category: str = Field(
        description="検知カテゴリ（例: impersonation, emergency_request, voice_synthesis）",
    )
    risk_score: float = Field(
        ge=0.0,
        le=1.0,
        description="危険度スコア（0.0〜1.0）",
    )
    risk_level: str = Field(
        default="suspicious",
        description="危険度ラベル（例: suspicious, likely_fake, authentic）",
    )
    reason: str = Field(
        min_length=1,
        max_length=1000,
        description="検知理由（ユーザー向けの平易な説明）",
    )
    advice: str | None = Field(
        default=None,
        max_length=500,
        description="推奨アクション（例: 一度電話を切って病院の代表番号に折り返す）",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="レコード作成日時 (UTC)",
    )
    expire_at: datetime | None = Field(
        default=None,
        description="TTL自動削除予定日時 (UTC)",
    )


class SessionCreate(BaseModel):
    """通話セッション開始リクエスト。"""

    session_id: str = Field(
        min_length=1,
        max_length=128,
        description="通話セッションID（UUIDやMeet通話識別子）",
    )
    started_at: datetime | None = Field(
        default=None,
        description="通話開始時刻（省略時はサーバー現在時刻）",
    )


class SessionEnd(BaseModel):
    """通話セッション終了リクエスト。"""

    analysis_incomplete: bool = False

    ended_at: datetime | None = Field(
        default=None,
        description="通話終了時刻（省略時はサーバー現在時刻）",
    )


class DetectionSaveResponse(BaseModel):
    """検知結果1件の保存結果を表す軽量レスポンス（全件再取得なし・O(1)レスポンス）。"""

    id: str = Field(description="保存された検知ID")
    session_id: str = Field(description="セッションID")
    status: str = Field(default="saved", description="保存ステータス")
    detections_count: int = Field(description="現在のセッション検知総数")
    max_risk_score: float = Field(description="現在のセッション最大危険度スコア")
    overall_verdict: SessionVerdict = Field(description="現在の総合判定")


class SessionEndResponse(BaseModel):
    """通話セッション終了結果を表す軽量レスポンス。"""

    session_id: str = Field(description="セッションID")
    ended_at: datetime = Field(description="通話終了時刻 (UTC)")
    status: str = Field(default="ended", description="終了ステータス")
    detections_count: int = Field(description="通話全体の検知総数")
    max_risk_score: float = Field(description="通話全体の最大危険度スコア")
    overall_verdict: SessionVerdict = Field(description="通話全体の総合判定")


class SessionResponse(BaseModel):
    """セッション情報・通話後レポートレスポンス。"""

    analysis_incomplete: bool = False
    session_id: str
    started_at: datetime
    ended_at: datetime | None = None
    max_risk_score: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="通話全体の最大危険度スコア",
    )
    overall_verdict: SessionVerdict = Field(
        default=SessionVerdict.AUTHENTIC,
        description="通話全体の総合判定",
    )
    detections_count: int = Field(
        default=0,
        ge=0,
        description="検知された不審箇所の件数",
    )
    expire_at: datetime = Field(
        description="TTL自動削除予定日時（デフォルト7日後）",
    )
    detections: list[DetectionItem] = Field(
        default_factory=list,
        description="検知レコード一覧",
    )

