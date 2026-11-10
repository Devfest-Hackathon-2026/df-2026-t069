"""pytest 共通フィクスチャ。[Role2]

テスト間の設定キャッシュ（lru_cache）汚染を防ぐため、各テストの前後にキャッシュをクリアする。
"""

import pytest  # type: ignore

from app.core.config import get_settings


@pytest.fixture(autouse=True)
def clear_settings_cache():
    """テスト実行前後に Settings の lru_cache をクリアし、テスト間の副作用を防ぐ。"""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()

