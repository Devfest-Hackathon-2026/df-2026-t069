"""FastAPI エントリポイント。"""

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.analyze import router as analyze_router
from app.api.v1.routes import router as v1_router
from app.core.config import get_settings

logging.basicConfig(level=logging.INFO)


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Impersonation Detection API", version="0.1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,  # 環境変数で指定したOriginのみ許可
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-API-Key"],
    )
    app.include_router(v1_router)
    app.include_router(analyze_router)

    @app.get("/healthz", summary="APIサーバーの死活確認")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
