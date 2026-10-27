"""Миграция отзыва согласия (спека #112, 3.2) на отдельном одноразовом Postgres.

На базе с данными до #112 колонки появляются пустыми: согласие действует, вход открыт.
Откат убирает только новые колонки, строки пациентов и согласий остаются.
"""

import asyncio

import asyncpg
from alembic import command
from alembic.config import Config
from testcontainers.community.postgres import PostgresContainer

from clin_compass.core.queue import asyncpg_dsn
from tests.conftest import API_DIR, PG_IMAGE

BEFORE = "407adb8a1dfb"
DOCTOR = "00000000-0000-4000-8000-0000000000d1"
PATIENT = "00000000-0000-4000-8000-0000000000a1"


async def seed_before(dsn: str) -> None:
    """Врач, пациент и его согласие — как после регистрации до #112."""
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
        await connection.execute(
            "INSERT INTO patients.patients (user_id, lead_doctor_id) VALUES ($1, $2)",
            PATIENT,
            DOCTOR,
        )
        await connection.execute(
            "INSERT INTO patients.consents (patient_id, kind, version)"
            " VALUES ($1, 'personal_data', '2026-10-16-draft')",
            PATIENT,
        )
    finally:
        await connection.close()


async def fetch(dsn: str, query: str) -> list[dict[str, object]]:
    connection = await asyncpg.connect(dsn)
    try:
        return [dict(row) for row in await connection.fetch(query)]
    finally:
        await connection.close()


def test_consent_revocation_migration_keeps_existing_rows_and_rolls_back() -> None:
    with PostgresContainer(PG_IMAGE, driver="asyncpg") as postgres:
        config = Config(str(API_DIR / "alembic.ini"))
        url = postgres.get_connection_url()
        dsn = asyncpg_dsn(url)
        config.attributes["database_url"] = url
        command.upgrade(config, BEFORE)
        asyncio.run(seed_before(dsn))

        command.upgrade(config, "head")
        users = asyncio.run(
            fetch(
                dsn, "SELECT role, disabled_at, disabled_reason FROM identity.users ORDER BY role"
            )
        )
        consents = asyncio.run(fetch(dsn, "SELECT kind, revoked_at FROM patients.consents"))

        command.downgrade(config, BEFORE)
        after_downgrade = asyncio.run(fetch(dsn, "SELECT kind, version FROM patients.consents"))
        command.upgrade(config, "head")
        command.check(config)

    assert users == [
        {"role": "doctor", "disabled_at": None, "disabled_reason": None},
        {"role": "patient", "disabled_at": None, "disabled_reason": None},
    ]
    assert consents == [{"kind": "personal_data", "revoked_at": None}]
    assert after_downgrade == [{"kind": "personal_data", "version": "2026-10-16-draft"}]
