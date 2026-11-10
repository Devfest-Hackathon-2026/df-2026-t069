"""発言テキスト分析API。mockまたはVertex AIで分析する。"""

import asyncio
import json
import sqlite3
from uuid import NAMESPACE_URL, uuid5

from fastapi import APIRouter, Depends, HTTPException, Response
from google.api_core.exceptions import GoogleAPIError
from google.auth.exceptions import GoogleAuthError

from app.api.v1.routes import get_session_service
from app.core.config import get_settings
from app.core.security import require_api_key
from app.schemas.analysis import AnalyzeRequest, AnalyzeResponse
from app.services.analysis_detection_mapper import to_detection_item
from app.services.analysis_service import AnalysisError, AnalysisService
from app.services.analysis_window_store import AnalysisWindowStore
from app.services.firestore_analysis_store import AnalysisStateError, FirestoreAnalysisWindowStore

router = APIRouter()


@router.post(
    "/analyze",
    response_model=AnalyzeResponse,
    dependencies=[Depends(require_api_key)],
    summary="発言分析",
    description="ANALYSIS_BACKENDに応じて固定モックまたはVertex AIで分析します。",
    responses={204: {"description": "件数待ち、または分析済み・分析中のIDを含むため待機"}},
)
async def analyze(request: AnalyzeRequest) -> AnalyzeResponse | Response:
    settings = get_settings()
    size = settings.analysis_context_utterances
    if len(request.utterances) < size:
        return Response(status_code=204)
    window = request.utterances[-size:]
    ids = [utterance.id for utterance in window]
    if len(set(ids)) != len(ids):
        raise HTTPException(status_code=422, detail="Utterance IDs must be unique in the window")

    async def state_operation(function, *args, **kwargs):
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(function, *args, **kwargs),
                timeout=settings.analysis_state_timeout_seconds,
            )
        except (
            OSError,
            sqlite3.Error,
            GoogleAPIError,
            GoogleAuthError,
            AnalysisStateError,
            TimeoutError,
            ValueError,
        ):
            raise HTTPException(
                status_code=503,
                detail="Analysis state store unavailable",
            ) from None

    def create_store():
        backend = settings.analysis_state_backend
        if backend == "auto":
            backend = "firestore" if settings.analysis_backend == "vertexai" else "sqlite"
        if backend == "firestore":
            return FirestoreAnalysisWindowStore(
                settings.gcp_project_id,
                settings.analysis_firestore_database,
            )
        return AnalysisWindowStore(settings.analysis_state_path)

    store = await state_operation(create_store)
    owner = await state_operation(
        store.claim,
        request.sessionId,
        ids,
        settings.analysis_timeout_seconds + 30,
        initial_ready=len(request.utterances) >= size,
    )
    if owner is None:
        return Response(status_code=204)
    success = False
    try:
        result = await AnalysisService(settings).analyze(request)
        try:
            detection = to_detection_item(result, window)
        except ValueError:
            raise HTTPException(
                status_code=502, detail="Analysis result cannot be saved",
            ) from None
        if detection is not None:
            # 保存成功後の状態更新失敗・通信切断でも、再送で同じ検知IDを使う。
            detection.id = str(uuid5(NAMESPACE_URL, json.dumps(
                ["analysis", request.sessionId, sorted(ids)], ensure_ascii=False,
            )))

            def save_detection():
                return get_session_service().record_detection(request.sessionId, detection)

            try:
                await asyncio.wait_for(
                    asyncio.to_thread(save_detection),
                    timeout=settings.analysis_state_timeout_seconds,
                )
            except KeyError:
                raise HTTPException(status_code=404, detail="Session not found") from None
            except (
                GoogleAPIError, GoogleAuthError, OSError, RuntimeError, ValueError, TimeoutError,
            ):
                raise HTTPException(
                    status_code=503, detail="Detection storage unavailable",
                ) from None
        success = True
        return result
    except AnalysisError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
    finally:
        await state_operation(store.finish, owner, success)
