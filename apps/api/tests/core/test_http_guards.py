"""Защитные слои HTTP: CSRF по Origin/Sec-Fetch-Site, ответ 500 и таймаут health."""

import asyncio
import time
from collections.abc import AsyncIterator

import httpx
import pytest
from asgi_lifespan import LifespanManager
from fastapi import FastAPI

from clin_compass.core.config import Settings
from clin_compass.main import create_app

LOGIN = "/api/v1/auth/login"
BODY = {"email": "nobody@example.com", "password": "whatever password"}


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "https://evil.example.com"},
        # Соседний поддомен того же сайта: SameSite=Lax его не остановит.
        {"Origin": "https://other.example.ru", "Sec-Fetch-Site": "same-site"},
        {"Sec-Fetch-Site": "cross-site"},
    ],
)
async def test_cross_origin_unsafe_request_is_rejected(
    client: httpx.AsyncClient, headers: dict[str, str]
) -> None:
    response = await client.post(LOGIN, json=BODY, headers=headers)

    assert response.status_code == 403
    assert response.headers["content-type"].startswith("application/problem+json")


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "http://test", "Sec-Fetch-Site": "same-origin"},
        {},  # не браузер: Origin нет, чужую cookie такой клиент не подставит
    ],
)
async def test_same_origin_or_non_browser_request_passes(
    client: httpx.AsyncClient, headers: dict[str, str]
) -> None:
    response = await client.post(LOGIN, json=BODY, headers=headers)

    # До проверки пароля дошли: 401, а не 403.
    assert response.status_code == 401


async def test_safe_method_is_not_blocked(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/health", headers={"Origin": "https://evil.example.com"})

    assert response.status_code == 200


async def test_unhandled_error_is_problem_json_without_details(app: FastAPI) -> None:
    async def explode() -> None:
        raise RuntimeError("secret internal detail")

    app.add_api_route("/api/v1/test-explode", explode)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with (
        LifespanManager(app),
        httpx.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        response = await client.get("/api/v1/test-explode")

    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/problem+json")
    assert "secret" not in response.text


@pytest.fixture
async def silent_server() -> AsyncIterator[int]:
    """TCP-сервер, который принимает соединение и молчит: так выглядит зависшая БД."""

    async def hold(_: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await asyncio.sleep(3600)
        writer.close()

    server = await asyncio.start_server(hold, "127.0.0.1", 0)
    yield server.sockets[0].getsockname()[1]
    server.close()


async def test_health_answers_503_quickly_when_database_hangs(silent_server: int) -> None:
    settings = Settings(
        database_url=f"postgresql+asyncpg://u:p@127.0.0.1:{silent_server}/db",
        environment="test",
        health_timeout_seconds=0.5,
        s3_endpoint_url=None,
        s3_access_key=None,
        s3_secret_key=None,
        s3_bucket=None,
        s3_region=None,
    )
    app = create_app(settings)
    async with (
        LifespanManager(app) as manager,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=manager.app), base_url="http://test"
        ) as client,
    ):
        started = time.perf_counter()
        response = await client.get("/api/v1/health")
        elapsed = time.perf_counter() - started

    assert response.status_code == 503
    assert elapsed < 2
