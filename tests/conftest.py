from collections.abc import Iterator

import pytest

from app.core.config import get_settings


@pytest.fixture(scope="session", autouse=True)
def test_settings(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("LOG_JSON", "false")
    # 测试必须离线且确定：禁用本地向量模型，向量路径由 FakeEmbeddingProvider 覆盖。
    monkeypatch.setenv("EMBEDDING_PROVIDER", "none")
    monkeypatch.setenv(
        "PAPER_STORAGE_DIR",
        str(tmp_path_factory.mktemp("paper-storage")),
    )
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
    monkeypatch.undo()
