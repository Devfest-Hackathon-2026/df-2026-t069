"""v1 API ルーティング。[Role2 & Role3]"""

import logging
from functools import lru_cache
from typing import Annotated

from fastapi import (  # type: ignore
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Path,
    UploadFile,
    status,
)

from app.core.config import Settings, get_settings
from app.core.security import read_validated_upload
from app.detectors.base import DetectorError, MediaInput
from app.detectors.factory import create_detector
from app.schemas.detection import DetectionResponse
from app.schemas.session import (
    DetectionItem,
    DetectionSaveResponse,
    SessionCreate,
    SessionEnd,
    SessionEndResponse,
    SessionResponse,
)
from app.schemas.transcription import TranscriptionResponse
from app.services.detection_service import DetectionService
from app.services.session_service import FirestoreSessionStore, SessionService
from app.services.transcription_service import TranscriptionService

router = APIRouter(prefix="/api/v1")
logger = logging.getLogger(__name__)


@lru_cache
def get_detection_service() -> DetectionService:
    return DetectionService(create_detector(get_settings()))


@lru_cache
def get_transcription_service() -> TranscriptionService:
    settings = get_settings()
    return TranscriptionService(
        project_id=settings.gcp_project_id,
        stt_backend=settings.stt_backend,
    )


@lru_cache
def get_session_service() -> SessionService:
    settings = get_settings()
    if settings.gcp_project_id:
        # 本番保存の失敗を、揮発性のインメモリ保存で成功扱いにしない。
        store = FirestoreSessionStore(project_id=settings.gcp_project_id)
        return SessionService(store=store)
    return SessionService()


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/transcribe", response_model=TranscriptionResponse)
async def transcribe(
    audio: Annotated[UploadFile, File(description="ステレオ音声チャンクファイル")],
    settings: Annotated[Settings, Depends(get_settings)],
    service: Annotated[TranscriptionService, Depends(get_transcription_service)],
    session_id: Annotated[str, Form(description="通話セッションID")] = "default",
    start_sec: Annotated[float, Form(description="通話開始からの経過秒数")] = 0.0,
) -> TranscriptionResponse:
    """通話音声チャンクを受け取り、左右チャンネルを分離して確定発言を返す [Issue #9]"""
    data, _ = await read_validated_upload(audio, settings.max_upload_bytes)
    try:
        return service.transcribe(data, session_id=session_id, start_sec=start_sec)
    except RuntimeError as exc:
        logger.error("STT transcription error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="Transcription service failed"
        ) from exc


@router.post("/detect", response_model=DetectionResponse)
async def detect(
    media: Annotated[UploadFile, File(description="音声・動画・画像ファイル")],
    settings: Annotated[Settings, Depends(get_settings)],
    service: Annotated[DetectionService, Depends(get_detection_service)],
    context: Annotated[str | None, Form(max_length=500)] = None,
) -> DetectionResponse:
    data, mime = await read_validated_upload(media, settings.max_upload_bytes)
    try:
        return await service.detect(MediaInput(data=data, mime_type=mime, context=context))
    except DetectorError as exc:
        # 内部エラーの詳細はログのみに残し、クライアントには汎用メッセージを返す
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="Detection failed"
        ) from exc


# --- セッション・分析結果保存 API [Issue #17] ---


@router.post("/sessions", response_model=SessionResponse, status_code=status.HTTP_201_CREATED)
async def create_session(
    body: SessionCreate,
    service: Annotated[SessionService, Depends(get_session_service)],
) -> SessionResponse:
    """通話セッションを開始し、初期レコードを作成する。"""
    return service.create_session(body.session_id, started_at=body.started_at)


@router.post("/sessions/{session_id}/detections", response_model=DetectionSaveResponse)
async def add_detection(
    session_id: Annotated[str, Path(description="セッションID")],
    body: DetectionItem,
    service: Annotated[SessionService, Depends(get_session_service)],
) -> DetectionSaveResponse:
    """セッションに検知結果を追加し、危険度スコアと総合判定を更新する（O(1)軽量保存・全件再取得なし）。"""
    try:
        return service.record_detection(session_id, body)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session '{session_id}' not found",
        ) from exc


@router.post("/sessions/{session_id}/end", response_model=SessionEndResponse)
async def end_session(
    session_id: Annotated[str, Path(description="セッションID")],
    body: SessionEnd,
    service: Annotated[SessionService, Depends(get_session_service)],
) -> SessionEndResponse:
    """通話セッションを終了状態にする（O(1)軽量終了・全件再取得なし）。"""
    try:
        return service.end_session(
            session_id, ended_at=body.ended_at, analysis_incomplete=body.analysis_incomplete,
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session '{session_id}' not found",
        ) from exc


@router.get("/sessions/{session_id}", response_model=SessionResponse)
async def get_session(
    session_id: Annotated[str, Path(description="セッションID")],
    service: Annotated[SessionService, Depends(get_session_service)],
) -> SessionResponse:
    """通話後レポート用にセッション情報と検知結果を取得する。"""
    try:
        return service.get_session(session_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session '{session_id}' not found",
        ) from exc
