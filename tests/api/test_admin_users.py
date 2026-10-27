from types import SimpleNamespace

from httpx import AsyncClient

from app.api.deps import get_current_user
from app.core.roles import UserRole
from app.models import User
from tests.api.conftest import TEST_PASSWORD, login

ADMIN_USERS = "/api/v1/admin/users"


async def test_admin_endpoints_require_login(client: AsyncClient) -> None:
    assert (await client.get(ADMIN_USERS)).status_code == 401
    assert (await client.get("/api/v1/admin/stats")).status_code == 401
    created = await client.post(
        ADMIN_USERS,
        json={"email": "x@example.com", "password": "password123"},
    )
    assert created.status_code == 401


async def test_reader_cannot_access_admin(reader_client: AsyncClient) -> None:
    responses = [
        await reader_client.get(ADMIN_USERS),
        await reader_client.post(
            ADMIN_USERS,
            json={"email": "x@example.com", "password": "password123"},
        ),
        await reader_client.patch(f"{ADMIN_USERS}/1", json={"display_name": "X"}),
        await reader_client.post(
            f"{ADMIN_USERS}/1/reset-password",
            json={"new_password": "password123"},
        ),
        await reader_client.get("/api/v1/admin/stats"),
    ]

    for response in responses:
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "forbidden"


async def test_create_reader(admin_client: AsyncClient) -> None:
    response = await admin_client.post(
        ADMIN_USERS,
        json={"email": "New@Example.com", "password": "secret123"},
    )

    assert response.status_code == 201
    user = response.json()
    assert user["email"] == "new@example.com"
    assert user["display_name"] == "new"
    assert user["role"] == "reader"
    assert user["is_active"] is True
    assert user["created_at"].endswith("Z")
    assert "password_hash" not in user
    assert "set-cookie" not in response.headers

    # 创建用户不会切换当前管理员会话。
    me = await admin_client.get("/api/v1/auth/me")
    assert me.json()["email"] == "admin@example.com"


async def test_create_user_display_name_rules(admin_client: AsyncClient) -> None:
    explicit = await admin_client.post(
        ADMIN_USERS,
        json={
            "email": "explicit@example.com",
            "password": "password123",
            "display_name": "  Alice  ",
        },
    )
    assert explicit.status_code == 201
    assert explicit.json()["display_name"] == "Alice"

    blank = await admin_client.post(
        ADMIN_USERS,
        json={
            "email": "blank@example.com",
            "password": "password123",
            "display_name": "   ",
        },
    )
    assert blank.status_code == 201
    assert blank.json()["display_name"] == "blank"

    long_name = "a" * 65
    rejected = await admin_client.post(
        ADMIN_USERS,
        json={
            "email": "long@example.com",
            "password": "password123",
            "display_name": long_name,
        },
    )
    assert rejected.status_code == 422


async def test_create_admin_and_duplicate_email(admin_client: AsyncClient) -> None:
    created = await admin_client.post(
        ADMIN_USERS,
        json={
            "email": "second-admin@example.com",
            "password": "password123",
            "role": "admin",
        },
    )
    assert created.status_code == 201
    assert created.json()["role"] == "admin"

    duplicate = await admin_client.post(
        ADMIN_USERS,
        json={"email": "SECOND-ADMIN@example.com", "password": "password123"},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "email_taken"


async def test_create_user_validation(admin_client: AsyncClient) -> None:
    short_password = await admin_client.post(
        ADMIN_USERS,
        json={"email": "short@example.com", "password": "short"},
    )
    assert short_password.status_code == 422

    unknown_field = await admin_client.post(
        ADMIN_USERS,
        json={"email": "extra@example.com", "password": "password123", "nickname": "x"},
    )
    assert unknown_field.status_code == 422


async def test_list_users_pagination_and_search(
    admin_client: AsyncClient,
    admin_user: User,
) -> None:
    for email in ("alice@example.com", "bob@test.org", "carol@test.org"):
        response = await admin_client.post(
            ADMIN_USERS,
            json={"email": email, "password": "password123"},
        )
        assert response.status_code == 201

    first_page = await admin_client.get(ADMIN_USERS, params={"page": 1, "page_size": 2})
    assert first_page.status_code == 200
    body = first_page.json()
    assert body["total"] == 4
    assert body["page"] == 1
    assert body["page_size"] == 2
    assert len(body["items"]) == 2
    ids = [item["id"] for item in body["items"]]
    assert ids == sorted(ids, reverse=True)

    second_page = await admin_client.get(ADMIN_USERS, params={"page": 2, "page_size": 2})
    assert len(second_page.json()["items"]) == 2

    beyond = await admin_client.get(ADMIN_USERS, params={"page": 99, "page_size": 2})
    assert beyond.status_code == 200
    assert beyond.json()["items"] == []

    by_email = await admin_client.get(ADMIN_USERS, params={"q": "alice"})
    assert [item["email"] for item in by_email.json()["items"]] == ["alice@example.com"]

    by_domain = await admin_client.get(ADMIN_USERS, params={"q": "test.org"})
    assert by_domain.json()["total"] == 2

    literal_wildcard = await admin_client.get(ADMIN_USERS, params={"q": "%"})
    assert literal_wildcard.json()["total"] == 0

    literal_underscore = await admin_client.get(ADMIN_USERS, params={"q": "_"})
    assert literal_underscore.json()["total"] == 0

    whitespace = await admin_client.get(ADMIN_USERS, params={"q": "   "})
    assert whitespace.json()["total"] == 4


async def test_update_user_fields(admin_client: AsyncClient, reader_user: User) -> None:
    response = await admin_client.patch(
        f"{ADMIN_USERS}/{reader_user.id}",
        json={"display_name": "  Renamed  "},
    )
    assert response.status_code == 200
    assert response.json()["display_name"] == "Renamed"

    promoted = await admin_client.patch(
        f"{ADMIN_USERS}/{reader_user.id}",
        json={"role": "admin"},
    )
    assert promoted.status_code == 200
    assert promoted.json()["role"] == "admin"

    cleared = await admin_client.patch(
        f"{ADMIN_USERS}/{reader_user.id}",
        json={"display_name": ""},
    )
    assert cleared.status_code == 200
    assert cleared.json()["display_name"] is None


async def test_update_user_rejects_empty_body(
    admin_client: AsyncClient,
    reader_user: User,
) -> None:
    response = await admin_client.patch(f"{ADMIN_USERS}/{reader_user.id}", json={})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "bad_request"


async def test_update_user_rejects_null_role(
    admin_client: AsyncClient,
    reader_user: User,
) -> None:
    response = await admin_client.patch(
        f"{ADMIN_USERS}/{reader_user.id}",
        json={"role": None},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_admin_cannot_demote_or_disable_self(admin_client: AsyncClient) -> None:
    demote = await admin_client.patch(
        f"{ADMIN_USERS}/1",
        json={"role": "reader"},
    )
    assert demote.status_code == 400
    assert demote.json()["error"]["code"] == "cannot_demote_self"

    disable = await admin_client.patch(
        f"{ADMIN_USERS}/1",
        json={"is_active": False},
    )
    assert disable.status_code == 400
    assert disable.json()["error"]["code"] == "cannot_disable_self"


async def test_cannot_remove_last_available_admin(
    app,
    admin_client: AsyncClient,
    admin_user: User,
) -> None:
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id=9999,
        role=UserRole.admin.value,
        is_active=True,
    )
    try:
        response = await admin_client.patch(
            f"{ADMIN_USERS}/{admin_user.id}",
            json={"role": "reader"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "last_admin"


async def test_update_or_reset_missing_user(admin_client: AsyncClient) -> None:
    patch = await admin_client.patch(f"{ADMIN_USERS}/999", json={"display_name": "X"})
    assert patch.status_code == 404
    assert patch.json()["error"]["code"] == "user_not_found"

    reset = await admin_client.post(
        f"{ADMIN_USERS}/999/reset-password",
        json={"new_password": "password123"},
    )
    assert reset.status_code == 404
    assert reset.json()["error"]["code"] == "user_not_found"


async def test_disable_revokes_sessions_and_blocks_login(
    admin_client: AsyncClient,
    reader_user: User,
    make_client,
) -> None:
    reader = await make_client()
    await login(reader, reader_user.email)
    assert (await reader.get("/api/v1/auth/me")).status_code == 200

    disabled = await admin_client.patch(
        f"{ADMIN_USERS}/{reader_user.id}",
        json={"is_active": False},
    )
    assert disabled.status_code == 200
    assert disabled.json()["is_active"] is False

    assert (await reader.get("/api/v1/auth/me")).status_code == 401

    blocked = await reader.post(
        "/api/v1/auth/login",
        json={"email": reader_user.email, "password": TEST_PASSWORD},
    )
    assert blocked.status_code == 401
    assert blocked.json()["error"]["code"] == "invalid_credentials"

    enabled = await admin_client.patch(
        f"{ADMIN_USERS}/{reader_user.id}",
        json={"is_active": True},
    )
    assert enabled.status_code == 200

    await login(reader, reader_user.email)


async def test_reset_password_revokes_all_sessions(
    admin_client: AsyncClient,
    reader_user: User,
    make_client,
) -> None:
    first_device = await make_client()
    second_device = await make_client()
    await login(first_device, reader_user.email)
    await login(second_device, reader_user.email)

    response = await admin_client.post(
        f"{ADMIN_USERS}/{reader_user.id}/reset-password",
        json={"new_password": "newpassword123"},
    )
    assert response.status_code == 204
    assert response.content == b""

    assert (await first_device.get("/api/v1/auth/me")).status_code == 401
    assert (await second_device.get("/api/v1/auth/me")).status_code == 401

    old_password = await first_device.post(
        "/api/v1/auth/login",
        json={"email": reader_user.email, "password": TEST_PASSWORD},
    )
    assert old_password.status_code == 401

    await login(first_device, reader_user.email, "newpassword123")


async def test_admin_stats(admin_client: AsyncClient, admin_user: User) -> None:
    await admin_client.post(
        ADMIN_USERS,
        json={"email": "active@example.com", "password": "password123"},
    )
    disabled = await admin_client.post(
        ADMIN_USERS,
        json={"email": "disabled@example.com", "password": "password123"},
    )
    await admin_client.patch(
        f"{ADMIN_USERS}/{disabled.json()['id']}",
        json={"is_active": False},
    )

    response = await admin_client.get("/api/v1/admin/stats")

    assert response.status_code == 200
    stats = response.json()
    assert stats["users_total"] == 3
    assert stats["users_active"] == 2
    assert stats["papers_total"] == 0
    assert stats["repos_total"] == 0
    assert stats["generated_at"].endswith("Z")
