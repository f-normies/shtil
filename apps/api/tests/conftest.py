"""Общие фикстуры: настоящий Postgres (pgvector) в testcontainers, миграции, HTTP-клиент."""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from pathlib import Path

import httpx
import pytest
import uvicorn
from alembic import command
from alembic.config import Config
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from testcontainers.community.postgres import PostgresContainer

from clin_compass.core.config import Settings
from clin_compass.core.db import make_engine, make_sessionmaker
from clin_compass.main import create_app
from clin_compass.modules.identity import service as identity
from clin_compass.modules.identity.schemas import Role, UserOut
from clin_compass.schema import metadata

# Тот же образ, что в infra/compose.dev.yaml.
PG_IMAGE = "pgvector/pgvector:0.8.7-pg18-trixie"
API_DIR = Path(__file__).resolve().parents[1]

MakeUser = Callable[..., Awaitable[UserOut]]


@pytest.fixture(scope="session")
def postgres_url() -> Iterator[str]:
    with PostgresContainer(PG_IMAGE, driver="asyncpg") as postgres:
        url = postgres.get_connection_url()
        config = Config(str(API_DIR / "alembic.ini"))
        config.attributes["database_url"] = url
        command.upgrade(config, "head")
        yield url


@pytest.fixture(scope="session")
def settings(postgres_url: str) -> Settings:
    # Значения передаём явно: Makefile экспортирует .env разработчика в тесты,
    # там могут стоять настоящие адреса узлов, его RustFS и каталог knowledge/.
    return Settings(
        database_url=postgres_url,
        environment="test",
        knowledge_dir=None,
        llm_urls=(),
        embeddings_urls=(),
        asr_urls=(),
        docparse_urls=(),
        # Зарезервированный домен .invalid не разрешается: тест не уйдёт в настоящий рубрикатор.
        knowledge_guidelines_api_url="http://guidelines.invalid/api.ashx",
        # Без S3 голосовые маршруты отвечают 503; тесты голоса подставляют своё хранилище.
        s3_endpoint_url=None,
        s3_access_key=None,
        s3_secret_key=None,
        s3_bucket=None,
        s3_region=None,
        # Сервер метрик тесты запускают сами, на свободном порту.
        metrics_port=None,
    )


@pytest.fixture(scope="session")
async def sessionmaker(settings: Settings) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = make_engine(settings.database_url)
    yield make_sessionmaker(engine)
    await engine.dispose()


@pytest.fixture(autouse=True)
async def clean_database(
    request: pytest.FixtureRequest,
) -> AsyncIterator[None]:
    """После теста очищает все таблицы приложения и очереди. Тесты без БД не трогает.

    URL берём у синхронной фикстуры, а движок создаём здесь: асинхронную session-фикстуру
    нельзя впервые получить через getfixturevalue изнутри работающего цикла событий.
    """
    if "postgres_url" not in request.fixturenames:
        yield
        return
    url: str = request.getfixturevalue("postgres_url")
    yield
    tables = [f'"{t.schema}"."{t.name}"' for t in metadata.sorted_tables]
    tables += ["pgq.pgqueuer", "pgq.pgqueuer_log"]
    engine = make_engine(url)
    async with engine.begin() as connection:
        # Триггеры запрещают TRUNCATE журнала доступа и таблиц alerts; в тестах обходим их
        # (суперпользователь).
        await connection.execute(text("SET LOCAL session_replication_role = replica"))
        await connection.execute(text(f"TRUNCATE {', '.join(tables)} CASCADE"))
    await engine.dispose()


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return create_app(settings)


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with (
        LifespanManager(app) as manager,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=manager.app), base_url="http://test"
        ) as http_client,
    ):
        yield http_client


LISTENERS = "SELECT count(*) FROM pg_stat_activity WHERE query ILIKE 'LISTEN%compass_events%'"


async def listener_count(maker: async_sessionmaker[AsyncSession]) -> int:
    async with maker() as session:
        return int(await session.scalar(text(LISTENERS)) or 0)


@pytest.fixture
async def live_server(
    app: FastAPI, sessionmaker: async_sessionmaker[AsyncSession]
) -> AsyncIterator[str]:
    """Настоящий uvicorn на свободном порту: для тестов WebSocket. Возвращает `host:port`."""
    listeners_before = await listener_count(sessionmaker)
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=0,
            lifespan="on",
            log_level="warning",
            ws_max_size=4096,
        )
    )
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.01)
    # Слушатель NOTIFY подключается в фоне: ждём его, иначе первое событие теста потеряется.
    while await listener_count(sessionmaker) <= listeners_before:
        await asyncio.sleep(0.02)
    port = server.servers[0].sockets[0].getsockname()[1]
    yield f"127.0.0.1:{port}"
    server.should_exit = True
    await task


@pytest.fixture
async def live_servers(
    settings: Settings, sessionmaker: async_sessionmaker[AsyncSession]
) -> AsyncIterator[tuple[str, str]]:
    """Два независимых API с общим Postgres и настоящими Uvicorn WebSocket transport."""
    listeners_before = await listener_count(sessionmaker)
    servers = [
        uvicorn.Server(
            uvicorn.Config(
                create_app(settings),
                host="127.0.0.1",
                port=0,
                lifespan="on",
                log_level="warning",
                ws_max_size=4096,
            )
        )
        for _ in range(2)
    ]
    tasks = [asyncio.create_task(server.serve()) for server in servers]

    async def wait_ready() -> tuple[str, str]:
        while not all(server.started for server in servers):
            await asyncio.sleep(0.01)
        while await listener_count(sessionmaker) < listeners_before + 2:
            await asyncio.sleep(0.02)
        return tuple(
            f"127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}" for server in servers
        )  # type: ignore[return-value]

    try:
        hosts = await asyncio.wait_for(wait_ready(), timeout=10)
        yield hosts
    finally:
        for server in servers:
            server.should_exit = True
        await asyncio.gather(*tasks)


@pytest.fixture
def make_user(sessionmaker: async_sessionmaker[AsyncSession]) -> MakeUser:
    """Создаёт пользователя напрямую через сервис identity (так в проде работает CLI)."""

    async def _make(
        email: str = "doctor@example.com",
        password: str = "correct horse battery staple",
        role: Role = Role.DOCTOR,
        full_name: str = "Иванов Иван Иванович",
    ) -> UserOut:
        async with sessionmaker() as session:
            user = await identity.create_user(
                session, email=email, password=password, role=role, full_name=full_name
            )
            await session.commit()
            return user

    return _make
