"""Vertex AI経由でGeminiのテキスト生成を確認する疎通テスト。"""

import asyncio

from google import genai
from google.genai import types

from app.core.config import get_settings


async def main() -> None:
    settings = get_settings()

    if not settings.gemini_use_vertexai:
        raise RuntimeError("GEMINI_USE_VERTEXAI=true に設定してください")

    if not settings.gcp_project_id:
        raise RuntimeError("GCP_PROJECT_ID が設定されていません")

    client = genai.Client(
        vertexai=True,
        project=settings.gcp_project_id,
        location=settings.gcp_location,
    )

    try:
        response = await client.aio.models.generate_content(
            model=settings.gemini_model,
            contents="「接続成功」と日本語で短く回答してください。",
            config=types.GenerateContentConfig(
                temperature=0,
                max_output_tokens=100,
            ),
        )

        print("Gemini connection: OK")
        print(f"Model: {settings.gemini_model}")
        print(f"Response: {response.text}")

    finally:
        await client.aio.aclose()


if __name__ == "__main__":
    asyncio.run(main())
