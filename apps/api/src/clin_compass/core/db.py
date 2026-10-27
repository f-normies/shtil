"""Подключение к Postgres: асинхронный движок SQLAlchemy и фабрика сессий."""

import asyncio

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def make_engine(database_url: str) -> AsyncEngine:
    return create_async_engine(database_url, pool_pre_ping=True)


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def database_is_reachable(engine: AsyncEngine, limit_seconds: float) -> bool:
    """Проверка для health: БД отвечает на простой запрос не дольше `limit_seconds` секунд."""
    try:
        async with asyncio.timeout(limit_seconds), engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except (OperationalError, DBAPIError, OSError, TimeoutError):
        return False
    return True
