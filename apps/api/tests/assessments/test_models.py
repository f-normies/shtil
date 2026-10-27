"""Хранилище результатов: снимки, идемпотентность, индексы и запрет изменений."""

import pytest
from httpx import AsyncClient
from sqlalchemy import UniqueConstraint, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.assessments.conftest import BASE, AssessmentCare, login, submission

from clin_compass.schema import metadata


def test_results_table_has_snapshots_idempotency_and_lookup_indexes() -> None:
    table = metadata.tables.get("assessments.results")

    assert table is not None, "таблица результатов должна быть зарегистрирована для Alembic"
    assert {
        "patient_id",
        "submission_id",
        "scale_id",
        "scale_version",
        "scale_title",
        "completed_at",
        "received_at",
        "answers",
        "scores",
        "definition_snapshot",
        "sources_snapshot",
    }.issubset(table.columns.keys())
    unique_keys = {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert ("patient_id", "submission_id") in unique_keys
    indexes = {tuple(column.name for column in index.columns) for index in table.indexes}
    assert ("patient_id", "id") in indexes
    assert ("scale_id", "scale_version", "id") in indexes


async def test_additive_migration_creates_results_table(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with sessionmaker() as session:
        table = await session.scalar(text("SELECT to_regclass('assessments.results')"))

    assert table is not None


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE assessments.results SET scale_title = 'Подменённое название'",
        "DELETE FROM assessments.results",
        "TRUNCATE assessments.results",
    ],
)
async def test_results_are_append_only_in_the_database(
    assessment_client: AsyncClient,
    assessment_care: AssessmentCare,
    sessionmaker: async_sessionmaker[AsyncSession],
    statement: str,
) -> None:
    await login(assessment_client, assessment_care.patient.email)
    created = await assessment_client.post(BASE, json=submission(str(assessment_care.patient.id)))
    assert created.status_code == 201

    async with sessionmaker() as session:
        with pytest.raises(DBAPIError, match="append-only"):
            await session.execute(text(statement))
        await session.rollback()
        count = await session.scalar(text("SELECT count(*) FROM assessments.results"))

    assert count == 1
