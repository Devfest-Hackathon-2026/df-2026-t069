"""アップロードされたメディアの検証。[Role4 / Role3]

- 受け入れる MIME タイプをホワイトリストで限定
- サイズ上限を「読み込みながら」判定し、巨大ファイルでメモリを食い潰されるのを防ぐ
- メディアはメモリ上でのみ扱い、ディスクやログには保存しない（プライバシー保護）
"""

import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, UploadFile, status
from fastapi.security import APIKeyHeader

from app.core.config import Settings, get_settings

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def require_api_key(
    api_key: Annotated[str | None, Depends(_api_key_header)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    """分析APIの共有キーを検証する。キーをログやエラー本文に含めない。"""
    if not settings.backend_api_key.strip():
        raise HTTPException(status_code=503, detail="API authentication is not configured")
    if not api_key or not secrets.compare_digest(
        api_key.encode("utf-8"), settings.backend_api_key.encode("utf-8")
    ):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")

ALLOWED_MIME_TYPES: frozenset[str] = frozenset(
    {
        "audio/wav",
        "audio/x-wav",
        "audio/mpeg",
        "audio/mp4",
        "audio/webm",
        "audio/ogg",
        "video/mp4",
        "video/webm",
        "image/jpeg",
        "image/png",
        "image/webp",
    }
)

_CHUNK_SIZE = 1024 * 1024


def normalize_mime(content_type: str | None) -> str:
    # "audio/webm;codecs=opus" -> "audio/webm"
    return (content_type or "").split(";", 1)[0].strip().lower()


async def read_validated_upload(file: UploadFile, max_bytes: int) -> tuple[bytes, str]:
    mime = normalize_mime(file.content_type)
    if mime not in ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported media type: {mime or 'unknown'}",
        )

    buf = bytearray()
    while chunk := await file.read(_CHUNK_SIZE):
        buf.extend(chunk)
        if len(buf) > max_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=f"File too large (max {max_bytes} bytes)",
            )
    if not buf:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Empty file")
    return bytes(buf), mime
