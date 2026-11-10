"""文字起こしAPIスキーマ [Issue #9]

フロントエンド・Chrome拡張機能とのデータ契約。
"""

from typing import Literal

from pydantic import BaseModel, Field  # type: ignore


class Utterance(BaseModel):
    """文単位で区切られた確定発言データ。"""

    id: str = Field(description="一意な発言ID (例: utt_1)")
    text: str = Field(description="発言テキスト")
    start_sec: float = Field(description="通話開始からの経過秒数")
    speaker: Literal["相手", "自分"] = Field(description="話者（ステレオ分離により確定）")
    is_final: bool = Field(default=True, description="確定フラグ")


class TranscriptionResponse(BaseModel):
    """文字起こしレスポンス。"""

    utterances: list[Utterance] = Field(
        default_factory=list,
        description="今回新しく確定した発言一覧",
    )
    recent_context: str = Field(
        description="直近5文程度を結合した文脈テキスト（Gemini不審判定用）",
    )
    has_opponent_speech: bool = Field(description="相手の発言が含まれているか")
    processing_ms: int = Field(description="処理時間 (ミリ秒)")

