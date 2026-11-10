"""検知器の抽象基底クラス（Strategy パターン）。[Role3]

新しい検知手法（例: 専用の音声ディープフェイク検知モデル、複数検知器のアンサンブル）は
Detector を継承して追加するだけで、API層・サービス層を変更せずに差し替えられる。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.schemas.detection import DetectionResult


@dataclass(frozen=True, slots=True)
class MediaInput:
    data: bytes
    mime_type: str
    # ユーザーが入力した状況説明（例:「息子を名乗る電話」）。信頼できない入力として扱う
    context: str | None = None

    @property
    def modality(self) -> str:
        return self.mime_type.split("/", 1)[0]  # audio / video / image


class Detector(ABC):
    """すべての検知器が満たすべきインターフェース。"""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    def model(self) -> str | None:
        return None

    @abstractmethod
    async def analyze(self, media: MediaInput) -> DetectionResult:
        """メディアを解析して判定結果を返す。失敗時は DetectorError を送出する。"""


class DetectorError(RuntimeError):
    """検知器内部のエラー。外部APIの詳細をクライアントに漏らさないためにラップする。"""
