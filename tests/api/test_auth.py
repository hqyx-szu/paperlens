from httpx import AsyncClient

from app.models import User
from tests.api.conftest import TEST_PASSWORD


async def test_login_returns_user_role_and_cookie(client: AsyncClient, reader_user: User) -> None:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "Reader@Example.com", "password": TEST_PASSWORD},
    )

    assert response.status_code == 200
    user = response.json()["user"]
    assert user["email"] == "reader@example.com"
    assert user["display_name"] == "Reader"
    assert user["role"] == "reader"
    assert user["is_active"] is True
    assert user["created_at"].endswith("Z")
    assert "password" not in user
    assert "password_hash" not in user
    assert response.cookies.get("paperlens_session")


async def test_login_rejects_wrong_password(client: AsyncClient, reader_user: User) -> None:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": reader_user.email, "password": "wrong-password"},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_credentials"
    assert response.json()["error"]["details"] is None


async def test_disabled_user_cannot_login(
    client: AsyncClient,
    reader_user: User,
) -> None:
    from sqlalchemy import update

    from app.db import get_session_factory

    async with get_session_factory()() as db:
        await db.execute(update(User).where(User.id == reader_user.id).values(is_active=False))
        await db.commit()

    response = await client.post(
        "/api/v1/auth/login",
        json={"email": reader_user.email, "password": TEST_PASSWORD},
    )

    assert response.status_code == 401
    error = response.json()["error"]
    assert error["code"] == "invalid_credentials"
    assert error["message"] == "邮箱或密码错误"


async def test_logout_revokes_current_session(
    client: AsyncClient,
    reader_user: User,
) -> None:
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": reader_user.email, "password": TEST_PASSWORD},
    )
    assert login.status_code == 200

    logout = await client.post("/api/v1/auth/logout")
    assert logout.status_code == 204
    assert logout.content == b""

    me = await client.get("/api/v1/auth/me")
    assert me.status_code == 401
    assert me.json()["error"]["code"] == "unauthorized"


async def test_me_requires_login(client: AsyncClient) -> None:
    response = await client.get("/api/v1/auth/me")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


async def test_me_returns_admin_role(admin_client: AsyncClient) -> None:
    response = await admin_client.get("/api/v1/auth/me")

    assert response.status_code == 200
    assert response.json()["role"] == "admin"


async def test_public_registration_is_removed(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": "new@example.com", "password": "password123"},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


async def test_login_validation(client: AsyncClient, reader_user: User) -> None:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": reader_user.email, "password": "x" * 129},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert response.json()["error"]["details"][0]["field"] == "body.password"

    unknown_field = await client.post(
        "/api/v1/auth/login",
        json={"email": reader_user.email, "password": TEST_PASSWORD, "remember": True},
    )
    assert unknown_field.status_code == 422
