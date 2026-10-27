"""Миграция состояний сигнала (спека #95) на отдельном одноразовом Postgres с данными до #95.

До #95 сигнал был открыт, пока не подтверждён: новое срабатывание после подтверждения
создавало второй сигнал по тому же правилу. Миграция закрывает такие старые сигналы,
иначе уникальный индекс «один нерешённый сигнал на правило» не создаётся.
"""

import asyncio
from datetime import UTC, datetime

import asyncpg
from alembic import command
from alembic.config import Config
from testcontainers.community.postgres import PostgresContainer

from clin_compass.core.queue import asyncpg_dsn
from tests.conftest import API_DIR, PG_IMAGE

BEFORE = "ed8b56d79336"
DOCTOR = "00000000-0000-4000-8000-0000000000d1"
PATIENT = "00000000-0000-4000-8000-0000000000a1"


def at(day: int, hour: int) -> datetime:
    """Время октября 2026 года: asyncpg передаёт timestamptz только объектом datetime."""
    return datetime(2026, 10, day, hour, tzinfo=UTC)


async def seed_before(dsn: str) -> None:
    """Врач подтвердил сигнал, пациент снова прошёл шкалу: два сигнала по правилу."""
    connection = await asyncpg.connect(dsn)
    try:
        for user_id, role in ((DOCTOR, "doctor"), (PATIENT, "patient")):
            await connection.execute(
                "INSERT INTO identity.users (id, email, password_hash, role, full_name)"
                " VALUES ($1, $2, 'x', $3, 'Синтетический')",
                user_id,
                f"{role}@example.invalid",
                role,
            )
        for name, acknowledged, created in (
            ("old", at(10, 10), at(10, 9)),
            ("new", None, at(12, 9)),
            ("other", at(11, 10), at(11, 9)),
        ):
            await connection.execute(
                """
                INSERT INTO alerts.alerts (patient_id, doctor_id, origin_type, rule_id,
                    rule_version, kind, level, title, doctor_actions, sources, created_at,
                    acknowledged_at, acknowledged_by)
                VALUES ($1, $2, 'rule', $3, 1, 'alert', 'mandatory', $4,
                    '["Связаться"]', '[{"source": "s", "locator": "1"}]',
                    $5, $6, $7)
                """,
                PATIENT,
                DOCTOR,
                "other-rule" if name == "other" else "phq9-suicidal-thoughts",
                name,
                created,
                acknowledged,
                DOCTOR if acknowledged else None,
            )
    finally:
        await connection.close()


async def states(dsn: str) -> dict[str, tuple[str, int]]:
    connection = await asyncpg.connect(dsn)
    try:
        rows = await connection.fetch(
            "SELECT a.title, a.state, count(x.id) AS actions FROM alerts.alerts a"
            " LEFT JOIN alerts.actions x ON x.alert_id = a.id GROUP BY a.title, a.state"
        )
        return {r["title"]: (r["state"], r["actions"]) for r in rows}
    finally:
        await connection.close()


def test_migration_closes_signals_superseded_before_95() -> None:
    with PostgresContainer(PG_IMAGE, driver="asyncpg") as postgres:
        config = Config(str(API_DIR / "alembic.ini"))
        url = postgres.get_connection_url()
        config.attributes["database_url"] = url
        command.upgrade(config, BEFORE)
        asyncio.run(seed_before(asyncpg_dsn(url)))

        command.upgrade(config, "head")

        # Подтверждённый сигнал, за которым по правилу есть более поздний, — решён записью
        # в истории; новый остаётся новым; подтверждённый без повтора — в работе.
        assert asyncio.run(states(asyncpg_dsn(url))) == {
            "old": ("resolved", 1),
            "new": ("new", 0),
            "other": ("in_work", 0),
        }
        command.check(config)
