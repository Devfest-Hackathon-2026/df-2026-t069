"""アプリ設定。値はすべて環境変数から読み込み、コードに秘密情報を書かない。[Role4]"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    detector_backend: Literal["gemini", "mock"] = "mock"
    stt_backend: Literal["gcp", "mock"] = "mock"
    backend_api_key: str = Field(default="", repr=False)
    analysis_backend: Literal["mock", "vertexai"] = "mock"
    analysis_timeout_seconds: float = Field(default=20, gt=0, le=120)
    analysis_max_retries: int = Field(default=1, ge=0, le=2)
    analysis_context_utterances: int = Field(default=5, ge=1, le=50)
    analysis_state_backend: Literal["auto", "sqlite", "firestore"] = "auto"
    analysis_firestore_database: str = "(default)"
    analysis_state_timeout_seconds: float = Field(default=10, gt=0, le=30)
    analysis_state_path: Path = (
        Path(__file__).resolve().parents[3] / "output" / "analysis-state" / "state.sqlite3"
    )

    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.5-flash"
    gemini_use_vertexai: bool = False
    gcp_project_id: str = Field(
        default="",
        validation_alias=AliasChoices("GCP_PROJECT_ID", "GOOGLE_CLOUD_PROJECT"),
    )
    gcp_location: str = Field(
        default="asia-northeast1",
        validation_alias=AliasChoices("GCP_LOCATION", "GOOGLE_CLOUD_LOCATION"),
    )

    max_upload_bytes: int = Field(default=15_000_000, gt=0)
    cors_allow_origins: str = "http://localhost:3000"

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.cors_allow_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
