"""Roundtrip миграции времени записи у разбора (спека #96) на одноразовом Postgres."""

from alembic import command
from alembic.config import Config
from testcontainers.community.postgres import PostgresContainer

from tests.conftest import API_DIR, PG_IMAGE

# Ревизия перед `freetext.analyses.recorded_at`.
BEFORE_RECORDED_AT = "407adb8a1dfb"


def test_recorded_at_migration_roundtrips_in_disposable_postgres() -> None:
    """Накатывает миграции, откатывает столбец, накатывает снова и сверяет с моделями."""
    with PostgresContainer(PG_IMAGE, driver="asyncpg") as postgres:
        config = Config(str(API_DIR / "alembic.ini"))
        config.attributes["database_url"] = postgres.get_connection_url()
        command.upgrade(config, "head")
        command.downgrade(config, BEFORE_RECORDED_AT)
        command.upgrade(config, "head")
        command.check(config)
