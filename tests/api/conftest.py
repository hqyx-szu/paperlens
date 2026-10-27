import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.core.roles import UserRole
from app.core.security import hash_password
from app.db import get_engine, get_session_factory, reset_engine
from app.main import create_app
from app.models import Base, User

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TEST_PASSWORD = "password123"

type CreateUser = Callable[..., Awaitable[User]]


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
def migrate_database(test_settings: None) -> None:
    asyncio.run(_reset_and_migrate())


@pytest.fixture(autouse=True)
async def prepare_database(migrate_database: None) -> AsyncIterator[None]:
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(text("SET FOREIGN_KEY_CHECKS=0"))
        for table in reversed(Base.metadata.sorted_tables):
            await conn.execute(text(f"TRUNCATE TABLE `{table.name}`"))
        await conn.execute(text("SET FOREIGN_KEY_CHECKS=1"))
    try:
        yield
    finally:
        await reset_engine()


@pytest.fixture
def app() -> FastAPI:
    return create_app()


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest.fixture
async def make_client(app: FastAPI) -> AsyncIterator[Callable[[], Awaitable[AsyncClient]]]:
    clients: list[AsyncClient] = []

    async def _make() -> AsyncClient:
        client = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")
        clients.append(client)
        return client

    yield _make
    for client in clients:
        await client.aclose()


@pytest.fixture
def user_factory() -> CreateUser:
    async def _create(
        *,
        email: str = "user@example.com",
        password: str = TEST_PASSWORD,
        display_name: str | None = None,
        role: UserRole | str = UserRole.reader,
        is_active: bool = True,
    ) -> User:
        async with get_session_factory()() as db:
            user = User(
                email=email.lower(),
                password_hash=hash_password(password),
                display_name=display_name,
                role=role.value if isinstance(role, UserRole) else role,
                is_active=is_active,
            )
            db.add(user)
            await db.commit()
            await db.refresh(user)
            return user

    return _create


@pytest.fixture
async def admin_user(user_factory: CreateUser) -> User:
    return await user_factory(
        email="admin@example.com",
        display_name="Admin",
        role=UserRole.admin,
    )


@pytest.fixture
async def reader_user(user_factory: CreateUser) -> User:
    return await user_factory(email="reader@example.com", display_name="Reader")


async def login(client: AsyncClient, email: str, password: str = TEST_PASSWORD) -> None:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert response.status_code == 200, response.text


@pytest.fixture
async def admin_client(client: AsyncClient, admin_user: User) -> AsyncClient:
    await login(client, admin_user.email)
    return client


@pytest.fixture
async def reader_client(client: AsyncClient, reader_user: User) -> AsyncClient:
    await login(client, reader_user.email)
    return client
