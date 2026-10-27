"""Команды дневника: повторы, ежедневная уникальность и версии."""

import asyncio
from datetime import datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.modules.diary import service as diary_service
from clin_compass.modules.diary.errors import DiaryError
from clin_compass.modules.diary.schemas import DiaryAudioCreateIn, DiaryVoiceEntryCreateIn
from tests.diary.conftest import DiaryCare, create_care, login

TZ = ZoneInfo("Europe/Moscow")


def command(kind: str, **fields: object) -> dict[str, object]:
    now = datetime.now(TZ).replace(microsecond=0)
    payload: dict[str, object] = {
        "operation_id": str(uuid4()),
        "entry_id": str(uuid4()),
        "kind": kind,
        "occurred_at": now.isoformat(),
    }
    payload.update(fields)
    return payload


async def test_daily_replay_is_one_entry_and_context_counts_it(
    client: httpx.AsyncClient, diary_care: DiaryCare
) -> None:
    await login(client, diary_care.patient.email)
    now = datetime.now(TZ).replace(microsecond=0)
    body = command(
        "daily",
        daily_on=now.date().isoformat(),
        occurred_at=now.isoformat(),
        mood_level=3,
        mood_words=["спокойствие"],
        text="День прошёл обычно.",
    )

    created = await client.post("/api/v1/diary/entries", json=body)
    replay = await client.post("/api/v1/diary/entries", json=body)
    context = await client.get("/api/v1/diary/context")

    assert created.status_code == 201
    assert replay.status_code == 200
    assert replay.json() == created.json()
    assert context.json()["daily_status"] == "saved"
    assert context.json()["month_entry_count"] == 1


async def test_same_operation_id_with_different_body_conflicts(
    client: httpx.AsyncClient, diary_care: DiaryCare
) -> None:
    await login(client, diary_care.patient.email)
    body = command("text", text="Первая запись")
    first = await client.post("/api/v1/diary/entries", json=body)
    changed = {**body, "text": "Другой текст"}

    replay = await client.post("/api/v1/diary/entries", json=changed)

    assert first.status_code == 201
    assert replay.status_code == 409
    assert replay.json()["code"] == "operation_conflict"


async def test_create_replay_normalizes_omitted_defaults(
    client: httpx.AsyncClient, diary_care: DiaryCare
) -> None:
    await login(client, diary_care.patient.email)
    body = command("mood", mood_level=3)
    created = await client.post("/api/v1/diary/entries", json=body)
    replay = await client.post(
        "/api/v1/diary/entries",
        json={**body, "mood_words": [], "text": None, "other_medicine_text": None},
    )

    assert created.status_code == 201
    assert replay.status_code == 200
    assert replay.json() == created.json()


async def test_hidden_daily_remains_unique_and_disappears_from_feed(
    client: httpx.AsyncClient, diary_care: DiaryCare
) -> None:
    await login(client, diary_care.patient.email)
    now = datetime.now(TZ).replace(microsecond=0)
    body = command(
        "daily", daily_on=now.date().isoformat(), occurred_at=now.isoformat(), mood_level=4
    )
    created = await client.post("/api/v1/diary/entries", json=body)
    entry_id = created.json()["entry_id"]
    hidden = await client.post(
        f"/api/v1/diary/entries/{entry_id}/hide",
        json={"operation_id": str(uuid4()), "expected_revision": 1},
    )
    duplicate = await client.post(
        "/api/v1/diary/entries",
        json=command(
            "daily", daily_on=now.date().isoformat(), occurred_at=now.isoformat(), mood_level=2
        ),
    )
    feed = await client.get("/api/v1/diary/entries")

    assert created.status_code == 201
    assert hidden.status_code == 200
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "daily_exists"
    assert feed.json()["items"] == []


async def test_two_daily_creates_for_same_date_are_serialized(
    client: httpx.AsyncClient, diary_care: DiaryCare
) -> None:
    await login(client, diary_care.patient.email)
    now = datetime.now(TZ).replace(microsecond=0)
    first = command(
        "daily", daily_on=now.date().isoformat(), occurred_at=now.isoformat(), mood_level=2
    )
    second = command(
        "daily", daily_on=now.date().isoformat(), occurred_at=now.isoformat(), mood_level=5
    )

    results = await asyncio.gather(
        client.post("/api/v1/diary/entries", json=first),
        client.post("/api/v1/diary/entries", json=second),
    )

    assert sorted(result.status_code for result in results) == [201, 409]
    conflict = next(result for result in results if result.status_code == 409)
    assert conflict.json()["code"] == "daily_exists"


async def test_concurrent_edits_reject_stale_revision(
    client: httpx.AsyncClient, diary_care: DiaryCare
) -> None:
    await login(client, diary_care.patient.email)
    created = await client.post(
        "/api/v1/diary/entries", json=command("text", text="Исходный текст")
    )
    entry_id = created.json()["entry_id"]
    base = {"expected_revision": 1}

    responses = await asyncio.gather(
        client.patch(
            f"/api/v1/diary/entries/{entry_id}",
            json={**base, "operation_id": str(uuid4()), "text": "Вариант А"},
        ),
        client.patch(
            f"/api/v1/diary/entries/{entry_id}",
            json={**base, "operation_id": str(uuid4()), "text": "Вариант Б"},
        ),
    )

    assert sorted(response.status_code for response in responses) == [200, 409]
    conflict = next(response for response in responses if response.status_code == 409)
    assert conflict.json()["code"] == "revision_conflict"
    detail = await client.get(f"/api/v1/diary/entries/{entry_id}")
    assert detail.json()["revision"] == 2
    assert detail.json()["text"] in {"Вариант А", "Вариант Б"}


async def test_mood_entry_cannot_clear_its_required_level(
    client: httpx.AsyncClient, diary_care: DiaryCare
) -> None:
    await login(client, diary_care.patient.email)
    created = await client.post("/api/v1/diary/entries", json=command("mood", mood_level=4))
    entry_id = created.json()["entry_id"]

    cleared = await client.patch(
        f"/api/v1/diary/entries/{entry_id}",
        json={"operation_id": str(uuid4()), "expected_revision": 1, "mood_level": None},
    )
    current = await client.get(f"/api/v1/diary/entries/{entry_id}")

    assert created.status_code == 201
    assert cleared.status_code == 422
    assert cleared.json()["code"] == "mood_required"
    assert current.json()["revision"] == 1
    assert current.json()["mood_level"] == 4


async def test_non_daily_entry_cannot_add_other_medicine_text(
    client: httpx.AsyncClient, diary_care: DiaryCare
) -> None:
    await login(client, diary_care.patient.email)
    created = await client.post("/api/v1/diary/entries", json=command("text", text="Запись"))
    entry_id = created.json()["entry_id"]

    patched = await client.patch(
        f"/api/v1/diary/entries/{entry_id}",
        json={
            "operation_id": str(uuid4()),
            "expected_revision": 1,
            "other_medicine_text": "Принял неизвестный препарат.",
        },
    )
    current = await client.get(f"/api/v1/diary/entries/{entry_id}")

    assert created.status_code == 201
    assert patched.status_code == 422
    assert patched.json()["code"] == "other_medicine_only_daily"
    assert current.json()["revision"] == 1
    assert current.json()["other_medicine_text"] is None


async def test_voice_create_rejects_duplicate_entry_id(
    diary_care: DiaryCare,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    body = DiaryVoiceEntryCreateIn(
        operation_id=uuid4(),
        entry_id=uuid4(),
        kind="voice",
        occurred_at=datetime.now(TZ),
    )
    audio = DiaryAudioCreateIn(
        storage_key=f"diary/{diary_care.patient.id}/{uuid4()}.webm",
        sha256="a" * 64,
        byte_size=10,
        mime_type="audio/webm",
        duration_ms=1_000,
    )
    async with sessionmaker() as session:
        created, is_new = await diary_service.create_voice_entry(
            session, diary_care.patient, body, audio, timezone=TZ
        )
        await session.commit()

    duplicate_body = body.model_copy(update={"operation_id": uuid4()})
    duplicate_audio = audio.model_copy(
        update={"storage_key": f"diary/{diary_care.patient.id}/{uuid4()}.webm"}
    )
    async with sessionmaker() as session:
        with pytest.raises(DiaryError) as raised:
            await diary_service.create_voice_entry(
                session,
                diary_care.patient,
                duplicate_body,
                duplicate_audio,
                timezone=TZ,
            )

    assert is_new is True
    assert created.entry_id == body.entry_id
    assert raised.value.status == 409
    assert raised.value.code == "entry_exists"


@pytest.mark.parametrize("bad_mood", [0, 6, True, "3"])
async def test_mood_level_requires_an_integer_from_one_to_five(
    client: httpx.AsyncClient, diary_care: DiaryCare, bad_mood: object
) -> None:
    await login(client, diary_care.patient.email)
    now = datetime.now(TZ).replace(microsecond=0)
    body = command("mood", occurred_at=now.isoformat(), mood_level=bad_mood)

    response = await client.post("/api/v1/diary/entries", json=body)

    assert response.status_code == 422


async def test_foreign_entry_read_and_edit_are_audited_as_denials(
    client: httpx.AsyncClient,
    diary_care: DiaryCare,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await login(client, diary_care.patient.email)
    created = await client.post(
        "/api/v1/diary/entries",
        json=command("text", text="Синтетическая запись владельца"),
    )
    foreign = await create_care(
        sessionmaker,
        "foreign-doctor@example.com",
        "foreign-patient@example.com",
    )
    await login(client, foreign.patient.email)
    entry_id = created.json()["entry_id"]

    read = await client.get(f"/api/v1/diary/entries/{entry_id}")
    edit = await client.patch(
        f"/api/v1/diary/entries/{entry_id}",
        json={"operation_id": str(uuid4()), "expected_revision": 1, "text": "Изменённый текст"},
    )
    async with sessionmaker() as session:
        denied = (
            (
                await session.execute(
                    text(
                        "SELECT action FROM identity.access_log "
                        "WHERE patient_id = :id AND action LIKE 'diary.entry.%.denied' "
                        "ORDER BY id"
                    ),
                    {"id": diary_care.patient.id},
                )
            )
            .scalars()
            .all()
        )

    assert created.status_code == 201
    assert read.status_code == 404
    assert edit.status_code == 404
    assert denied == ["diary.entry.read.denied", "diary.entry.edit.denied"]
