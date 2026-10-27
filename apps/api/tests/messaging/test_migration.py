"""Roundtrip миграций messaging на отдельном одноразовом Postgres."""

from alembic import command
from alembic.config import Config
from testcontainers.community.postgres import PostgresContainer

from tests.conftest import API_DIR, PG_IMAGE


def test_messaging_migration_roundtrips_in_disposable_postgres() -> None:
    """Полностью накатывает миграции, откатывает messaging и накатывает их снова."""
    with PostgresContainer(PG_IMAGE, driver="asyncpg") as postgres:
        config = Config(str(API_DIR / "alembic.ini"))
        config.attributes["database_url"] = postgres.get_connection_url()
        command.upgrade(config, "head")
        command.downgrade(config, "9e4b1d7c3a52")
        command.upgrade(config, "head")
        command.check(config)
