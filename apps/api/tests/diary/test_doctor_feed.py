"""Доступ врача, диапазоны и точки настроения дневника."""

from datetime import date, datetime, time, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.modules.freetext import service as freetext
from tests.diary.conftest import DiaryCare, create_care, login

TZ = ZoneInfo("Europe/Moscow")


async def create_mood(client: httpx.AsyncClient, day: date, level: int) -> None:
    occurred_at = datetime.combine(day, time(12), TZ)
    response = await client.post(
        "/api/v1/diary/entries",
        json={
            "operation_id": str(uuid4()),
            "entry_id": str(uuid4()),
            "kind": "mood",
            "occurred_at": occurred_at.isoformat(),
            "mood_level": level,
        },
    )
    assert response.status_code == 201


async def test_unrelated_doctor_cannot_read_feed_and_denial_is_audited(
    client: httpx.AsyncClient,
    diary_care: DiaryCare,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    foreign = await create_care(sessionmaker, "other@example.com", "other-patient@example.com")
    await login(client, diary_care.doctor.email)
    today = datetime.now(TZ).date()
    start = today - timedelta(days=6)

    response = await client.get(
        f"/api/v1/patients/{foreign.patient.id}/diary",
        params={"start": start.isoformat(), "end": today.isoformat()},
    )
    async with sessionmaker() as session:
        rows = (
            (
                await session.execute(
                    text(
                        "SELECT action FROM identity.access_log WHERE patient_id = :id ORDER BY id"
                    ),
                    {"id": foreign.patient.id},
                )
            )
            .scalars()
            .all()
        )

    assert response.status_code == 404
    assert rows == ["diary.patient_feed.list.denied"]


async def test_mood_points_are_independent_of_feed_page_size(
    client: httpx.AsyncClient,
    diary_care: DiaryCare,
) -> None:
    await login(client, diary_care.patient.email)
    today = datetime.now(TZ).date()
    await create_mood(client, today - timedelta(days=1), 2)
    await create_mood(client, today, 5)
    await login(client, diary_care.doctor.email)
    params = {"start": (today - timedelta(days=6)).isoformat(), "end": today.isoformat()}

    feed = await client.get(
        f"/api/v1/patients/{diary_care.patient.id}/diary", params={**params, "limit": 1}
    )
    moods = await client.get(f"/api/v1/patients/{diary_care.patient.id}/diary/moods", params=params)

    assert feed.status_code == 200
    assert len(feed.json()["items"]) == 1
    assert feed.json()["next_cursor"] is not None
    assert moods.status_code == 200
    assert [point["mood_level"] for point in moods.json()["items"]] == [5, 2]


async def test_doctor_feed_batches_revision_and_source_reads_for_page(
    app: FastAPI,
    client: httpx.AsyncClient,
    diary_care: DiaryCare,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await login(client, diary_care.patient.email)
    occurred_at = datetime.now(TZ).replace(microsecond=0)
    for text_value in ("Запись первая", "Запись вторая", "Запись третья"):
        response = await client.post(
            "/api/v1/diary/entries",
            json={
                "operation_id": str(uuid4()),
                "entry_id": str(uuid4()),
                "kind": "text",
                "occurred_at": occurred_at.isoformat(),
                "text": text_value,
            },
        )
        assert response.status_code == 201

    status_calls: list[set[str]] = []
    source_calls: list[set[str]] = []
    original_statuses = freetext.statuses
    original_diary_sources = freetext.list_diary_sources

    async def count_statuses(session, *, source_type, source_ids):
        source_ids = list(source_ids)
        status_calls.append({str(source_id) for source_id in source_ids})
        return await original_statuses(session, source_type=source_type, source_ids=source_ids)

    async def count_diary_sources(session, *, patient_id, source_ids):
        source_ids = list(source_ids)
        source_calls.append({str(source_id) for source_id in source_ids})
        return await original_diary_sources(session, patient_id=patient_id, source_ids=source_ids)

    monkeypatch.setattr(freetext, "statuses", count_statuses)
    monkeypatch.setattr(freetext, "list_diary_sources", count_diary_sources)
    await login(client, diary_care.doctor.email)
    today = datetime.now(TZ).date()
    params = {"start": (today - timedelta(days=6)).isoformat(), "end": today.isoformat()}
    revision_queries: list[str] = []
    analysis_queries: list[str] = []

    def count_selects(_connection, _cursor, statement, _parameters, _context, _many) -> None:
        normalized = statement.casefold()
        if not normalized.lstrip().startswith("select"):
            return
        if "diary.revisions" in normalized:
            revision_queries.append(statement)
        if "freetext.analyses" in normalized:
            analysis_queries.append(statement)

    engine = app.state.engine.sync_engine
    event.listen(engine, "before_cursor_execute", count_selects)
    try:
        feed = await client.get(
            f"/api/v1/patients/{diary_care.patient.id}/diary",
            params={**params, "limit": 10},
        )
    finally:
        event.remove(engine, "before_cursor_execute", count_selects)

    assert feed.status_code == 200
    assert len(feed.json()["items"]) == 3
    page_source_ids = {
        source["source_id"] for item in feed.json()["items"] for source in item["sources"]
    }
    assert len(page_source_ids) == 3
    assert len(status_calls) == 1
    assert len(source_calls) == 1
    assert status_calls[0] == page_source_ids
    assert source_calls[0] == page_source_ids
    assert len(revision_queries) == 1
    assert len(analysis_queries) == 2


async def test_doctor_period_must_cover_seven_to_ninety_days_and_observation(
    client: httpx.AsyncClient, diary_care: DiaryCare
) -> None:
    await login(client, diary_care.doctor.email)
    today = datetime.now(TZ).date()
    path = f"/api/v1/patients/{diary_care.patient.id}/diary"

    too_short = await client.get(
        path,
        params={"start": (today - timedelta(days=5)).isoformat(), "end": today.isoformat()},
    )
    before_observation = await client.get(
        path,
        params={
            "start": (today - timedelta(days=13)).isoformat(),
            "end": (today - timedelta(days=7)).isoformat(),
        },
    )
    future_end = await client.get(
        path,
        params={
            "start": (today + timedelta(days=1)).isoformat(),
            "end": (today + timedelta(days=7)).isoformat(),
        },
    )

    assert [too_short.status_code, before_observation.status_code, future_end.status_code] == [
        422,
        422,
        422,
    ]
