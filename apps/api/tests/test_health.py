"""Health-эндпоинт: по нему деплой решает, жив ли стенд (спека фундамента, 6.5).

Узлы инференса показываются по ролям, но на статус не влияют (спека #18, раздел 6).
Раздел `knowledge` — только для сведения (спека #109, 4.3).
"""

import asyncio
import hashlib
from collections.abc import Callable, Coroutine
from typing import Any

import httpx
import pytest
import structlog
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.core.config import Settings
from clin_compass.main import create_app
from tests.core.fake_nodes import dead_node_url, fake_node, health_ok
from tests.knowledge.helpers import Piece, add_document

NO_NODES = {"llm": [], "embeddings": [], "asr": [], "docparse": [], "rerank": []}
EMPTY_INDEX = {"version": None, "documents": 0, "fragments": 0}


async def _get_health(
    settings: Settings, prepare: Callable[[FastAPI], None] | None = None
) -> httpx.Response:
    app = create_app(settings)
    if prepare is not None:
        prepare(app)
    async with (
        LifespanManager(app) as manager,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=manager.app), base_url="http://test"
        ) as client,
    ):
        return await client.get("/api/v1/health")


async def test_health_reports_ok_when_database_reachable(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "checks": {"database": "ok"},
        "inference": NO_NODES,
        "knowledge": EMPTY_INDEX,
    }


async def test_health_shows_inference_nodes_without_failing_on_them(settings: Settings) -> None:
    async with fake_node(health_ok) as live:
        dead = dead_node_url()
        response = await _get_health(
            settings.model_copy(
                update={"llm_urls": (dead,), "embeddings_urls": (live.url, dead), "asr_urls": ()}
            )
        )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["inference"] == {
        "llm": ["error"],
        "embeddings": ["ok", "error"],
        "asr": [],
        "docparse": [],
        "rerank": [],
    }
    # Эндпоинт открыт наружу: адреса узлов в ответ не попадают.
    assert "127.0.0.1" not in response.text


async def test_health_returns_503_when_database_unreachable() -> None:
    # Порт 1 на localhost заведомо закрыт: соединение с БД невозможно.
    settings = Settings(
        database_url="postgresql+asyncpg://nobody:nothing@127.0.0.1:1/none",
        environment="test",
        llm_urls=(),
        embeddings_urls=(),
        asr_urls=(),
        s3_endpoint_url=None,
        s3_access_key=None,
        s3_secret_key=None,
        s3_bucket=None,
        s3_region=None,
    )

    response = await _get_health(settings)

    assert response.status_code == 503
    assert response.json() == {
        "status": "error",
        "checks": {"database": "error"},
        "inference": NO_NODES,
        "knowledge": None,
    }


async def test_health_shows_knowledge_index_version_and_counts(
    client: httpx.AsyncClient, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    async with sessionmaker() as session:
        await add_document(
            session, "kr-test", [Piece("Первый."), Piece("Второй.")], content_hash="a" * 64
        )
        await session.commit()

    response = await client.get("/api/v1/health")

    assert response.status_code == 200
    version = hashlib.sha256(f"kr-test:{'a' * 64}".encode()).hexdigest()[:12]
    assert response.json()["knowledge"] == {"version": version, "documents": 1, "fragments": 2}


async def _fails(_: object) -> object:
    raise RuntimeError("сбой раздела")


async def _hangs(_: object) -> object:
    await asyncio.sleep(3600)
    return {}


@pytest.mark.parametrize("section", [_fails, _hangs], ids=["error", "timeout"])
async def test_failed_section_is_null_and_does_not_change_status(
    settings: Settings, section: Callable[[object], Coroutine[Any, Any, object]]
) -> None:
    def prepare(app: FastAPI) -> None:
        app.state.health_sections = {"knowledge": section}

    with structlog.testing.capture_logs() as logs:
        response = await _get_health(
            settings.model_copy(update={"health_timeout_seconds": 0.5}), prepare
        )

    assert response.status_code == 200
    body = response.json()
    assert (body["status"], body["knowledge"]) == ("ok", None)
    assert any(
        log["event"] == "health.section_failed"
        and log["section"] == "knowledge"
        and log["log_level"] == "warning"
        for log in logs
    )
