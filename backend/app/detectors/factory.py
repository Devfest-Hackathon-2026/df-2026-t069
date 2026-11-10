"""設定に応じて Detector の具象クラスを生成するファクトリ。[Role3]"""

from app.core.config import Settings
from app.detectors.base import Detector
from app.detectors.mock_detector import MockDetector


def create_detector(settings: Settings) -> Detector:
    if settings.detector_backend == "gemini":
        # google-genai の import を遅延させ、mock 利用時は依存を読み込まない
        from app.detectors.gemini_detector import GeminiDetector

        return GeminiDetector(
            model=settings.gemini_model,
            api_key=settings.gemini_api_key,
            use_vertexai=settings.gemini_use_vertexai,
            project=settings.gcp_project_id,
            location=settings.gcp_location,
        )
    return MockDetector()
