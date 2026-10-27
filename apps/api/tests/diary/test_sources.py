"""Текстовые источники остаются неизменными между версиями дневника."""

from datetime import datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.diary.conftest import DiaryCare, login


def text_command(text_value: str) -> dict[str, object]:
    return {
        "operation_id": str(uuid4()),
        "entry_id": str(uuid4()),
        "kind": "text",
        "occurred_at": datetime.now(ZoneInfo("Europe/Moscow")).replace(microsecond=0).isoformat(),
        "text": text_value,
    }


async def test_mood_edit_reuses_source_and_text_edit_starts_new_analysis(
    client: httpx.AsyncClient,
    diary_care: DiaryCare,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await login(client, diary_care.patient.email)
    created = await client.post(
        "/api/v1/diary/entries", json=text_command("Сегодня было тревожно.")
    )
    entry_id = created.json()["entry_id"]
    mood_edit = await client.patch(
        f"/api/v1/diary/entries/{entry_id}",
        json={"operation_id": str(uuid4()), "expected_revision": 1, "mood_level": 2},
    )

    async with sessionmaker() as session:
        sources_after_mood_edit = (
            (
                await session.execute(
                    text(
                        "SELECT text_source_id FROM diary.revisions "
                        "WHERE entry_id = :id ORDER BY revision"
                    ),
                    {"id": entry_id},
                )
            )
            .scalars()
            .all()
        )
        analyses_after_mood_edit = (
            await session.execute(
                text(
                    "SELECT source_id, text FROM freetext.analyses "
                    "WHERE source_type = 'diary' AND patient_id = :id"
                ),
                {"id": diary_care.patient.id},
            )
        ).all()

    text_edit = await client.patch(
        f"/api/v1/diary/entries/{entry_id}",
        json={
            "operation_id": str(uuid4()),
            "expected_revision": 2,
            "text": "Теперь стало спокойнее.",
        },
    )
    async with sessionmaker() as session:
        sources_after_text_edit = (
            (
                await session.execute(
                    text(
                        "SELECT text_source_id FROM diary.revisions "
                        "WHERE entry_id = :id ORDER BY revision"
                    ),
                    {"id": entry_id},
                )
            )
            .scalars()
            .all()
        )
        analyses = (
            await session.execute(
                text(
                    "SELECT source_id, text FROM freetext.analyses "
                    "WHERE source_type = 'diary' AND patient_id = :id"
                ),
                {"id": diary_care.patient.id},
            )
        ).all()

    assert created.status_code == 201
    assert mood_edit.status_code == 200
    assert len(sources_after_mood_edit) == 2
    assert sources_after_mood_edit[0] == sources_after_mood_edit[1]
    assert analyses_after_mood_edit == [(sources_after_mood_edit[0], "Сегодня было тревожно.")]
    assert text_edit.status_code == 200
    assert len(sources_after_text_edit) == 3
    assert sources_after_text_edit[0] == sources_after_text_edit[1]
    assert sources_after_text_edit[1] != sources_after_text_edit[2]
    assert set(analyses) == {
        (sources_after_text_edit[0], "Сегодня было тревожно."),
        (sources_after_text_edit[2], "Теперь стало спокойнее."),
    }


async def test_daily_narrative_and_other_medicine_have_separate_sources(
    client: httpx.AsyncClient,
    diary_care: DiaryCare,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await login(client, diary_care.patient.email)
    now = datetime.now(ZoneInfo("Europe/Moscow")).replace(microsecond=0)
    response = await client.post(
        "/api/v1/diary/entries",
        json={
            "operation_id": str(uuid4()),
            "entry_id": str(uuid4()),
            "kind": "daily",
            "occurred_at": now.isoformat(),
            "daily_on": now.date().isoformat(),
            "mood_level": 3,
            "text": "Погулял.",
            "other_medicine_text": "Принял таблетку из домашней аптечки.",
        },
    )
    entry_id = response.json()["entry_id"]
    async with sessionmaker() as session:
        sources = (
            await session.execute(
                text(
                    "SELECT text_source_id, other_medicine_source_id "
                    "FROM diary.revisions WHERE entry_id = :id"
                ),
                {"id": entry_id},
            )
        ).one()
        analyses = (
            await session.execute(
                text("SELECT source_id, text FROM freetext.analyses WHERE patient_id = :id"),
                {"id": diary_care.patient.id},
            )
        ).all()

    assert response.status_code == 201
    assert sources.text_source_id != sources.other_medicine_source_id
    assert set(analyses) == {
        (sources.text_source_id, "Погулял."),
        (sources.other_medicine_source_id, "Принял таблетку из домашней аптечки."),
    }
