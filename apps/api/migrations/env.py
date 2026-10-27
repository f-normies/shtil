"""Окружение Alembic.

URL БД: атрибут `database_url` (его передают тесты) или COMPASS_DATABASE_URL.
"""

import asyncio

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from clin_compass.core.config import Settings
from clin_compass.schema import metadata

config = context.config
target_metadata = metadata


def _database_url() -> str:
    url = config.attributes.get("database_url")
    if isinstance(url, str):
        return url
    return Settings().database_url  # type: ignore[call-arg]


# Схемы, которыми управляют не наши модели (PgQueuer ставится SQL-скриптом).
EXTERNAL_SCHEMAS = {"pgq"}


def _include_name(name: str | None, type_: str, _parent: object) -> bool:
    if type_ == "schema":
        return name not in EXTERNAL_SCHEMAS
    return True


def _configure(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_schemas=True,
        include_name=_include_name,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def _run_async_migrations() -> None:
    engine = create_async_engine(_database_url(), poolclass=pool.NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(_configure)
    await engine.dispose()


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        include_schemas=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(_run_async_migrations())
