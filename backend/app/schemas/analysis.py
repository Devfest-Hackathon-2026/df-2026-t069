"""発言テキスト分析の入出力契約（#11）。"""

from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator

RiskCategory = Literal["C1", "C2", "C3", "C4", "C5"]


class Utterance(BaseModel):
    """通話中の発言1件。"""

    id: str = Field(min_length=1, description="発言ID")
    text: str = Field(min_length=1, description="発言内容")
    startSec: float = Field(ge=0, allow_inf_nan=False, description="録音開始から発言開始までの秒数")


class AnalyzeRequest(BaseModel):
    """フロントから受け取り、判定処理に渡すデータ。"""

    sessionId: str = Field(min_length=1, description="通話セッションID")
    utterances: list[Utterance] = Field(min_length=1)


class AnalyzeResponse(BaseModel):
    """highのみ即時警告。根拠のあるlow/medium/highをレポートへ保存する。"""

    is_suspicious: bool
    risk_level: Literal["low", "medium", "high"]
    category: RiskCategory | None = Field(description="不審カテゴリのコード。該当なしはnull")
    quote: str | None = Field(min_length=1, description="該当発言。該当なしはnull")
    reason: str = Field(min_length=1)
    advice: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_risk_consistency(self) -> Self:
        if not self.is_suspicious:
            if self.risk_level != "low":
                raise ValueError("medium/high risk requires is_suspicious=true")
            if self.category is not None or self.quote is not None:
                raise ValueError("No concern requires null category and quote")
        elif self.category is None or self.quote is None or not self.quote.strip():
            raise ValueError("A concern requires category and non-blank quote")
        if not self.reason.strip() or not self.advice.strip():
            raise ValueError("reason and advice must not be blank")
        return self
