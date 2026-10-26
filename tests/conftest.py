import asyncio
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.core.config import get_settings
from app.db import get_engine, reset_engine
from app.main import create_app
from app.models import Base

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _upgrade_to_head(connection: Connection) -> None:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    config.attributes["connection"] = connection
    command.upgrade(config, "head")


async def _reset_and_migrate() -> None:
    await reset_engine()
    engine = get_engine()
    database_name = engine.url.database
    if database_name != "paperlens_test":
        raise RuntimeError(f"Refusing to run tests against non-test database: {database_name}")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
    async with engine.connect() as conn:
        await conn.run_sync(_upgrade_to_head)
    await reset_engine()


@pytest.fixture(scope="session", autouse=True)
def test_settings() -> Iterator[None]:
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("LOG_JSON", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
    monkeypatch.undo()


@pytest.fixture(scope="session", autouse=True)
def migrate_database(test_settings: None) -> None:
    asyncio.run(_reset_and_migrate())


@pytest.fixture(autouse=True)
async def prepare_database(migrate_database: None) -> AsyncIterator[None]:
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM user_sessions"))
        await conn.execute(text("DELETE FROM users"))
    try:
        yield
    finally:
        await reset_engine()


@pytest.fixture
def app() -> FastAPI:
    return create_app()


@pytest.fixture
async def client(app: FastAPI, prepare_database: None) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client
