from httpx import AsyncClient


async def test_live_health_returns_ok(client: AsyncClient) -> None:
    response = await client.get("/api/v1/health/live")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "paperlens",
        "version": "0.1.0",
    }


async def test_root_returns_app_info(client: AsyncClient) -> None:
    response = await client.get("/")

    assert response.status_code == 200
    assert response.json()["name"] == "PaperLens"


async def test_request_id_is_echoed(client: AsyncClient) -> None:
    response = await client.get(
        "/api/v1/health/live",
        headers={"X-Request-ID": "req-123"},
    )

    assert response.headers["X-Request-ID"] == "req-123"


async def test_request_id_is_generated(client: AsyncClient) -> None:
    response = await client.get("/api/v1/health/live")

    assert response.headers.get("X-Request-ID")


async def test_unknown_route_returns_error_envelope(client: AsyncClient) -> None:
    response = await client.get("/api/v1/does-not-exist")

    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "not_found"
    assert "traceback" not in str(body).lower()


async def test_ready_health_returns_ok(client: AsyncClient) -> None:
    response = await client.get("/api/v1/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


async def test_openapi_contains_health_path(client: AsyncClient) -> None:
    response = await client.get("/openapi.json")

    assert response.status_code == 200
    paths = response.json()["paths"]
    assert "/api/v1/health/live" in paths
    assert "/api/v1/health/ready" in paths
    assert "/api/v1/auth/login" in paths
