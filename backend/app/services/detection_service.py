"""検知ユースケース。API層と検知器の間に立ち、計測・共通処理を担う。[Role3]

将来ここに「複数検知器のアンサンブル」「結果の監査ログ（メディア本体は保存しない）」等を追加する。
"""

import time

from app.detectors.base import Detector, MediaInput
from app.schemas.detection import DetectionResponse


class DetectionService:
    def __init__(self, detector: Detector) -> None:
        self._detector = detector

    async def detect(self, media: MediaInput) -> DetectionResponse:
        started = time.perf_counter()
        result = await self._detector.analyze(media)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return DetectionResponse(
            **result.model_dump(),
            detector=self._detector.name,
            model=self._detector.model,
            processing_ms=elapsed_ms,
        )
