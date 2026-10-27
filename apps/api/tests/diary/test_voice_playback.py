"""HTTP-проверки закрытого воспроизведения голоса дневника."""

from uuid import uuid4

import httpx
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.modules.identity import service as identity
from clin_compass.modules.identity.schemas import Role
from tests.diary.conftest import PASSWORD, DiaryCare, create_care, login
from tests.diary.voice_support import WEBM, MemoryStorage, upload_voice, voice_metadata


async def _audio_events(
    sessionmaker: async_sessionmaker[AsyncSession], entry_id: str
) -> list[tuple[str, str | None, str | None]]:
    async with sessionmaker() as session:
        rows = await session.execute(
            text(
                "SELECT action, patient_id::text, subject FROM identity.access_log "
                "WHERE subject = :entry_id OR action LIKE 'diary.audio.read%' ORDER BY id"
            ),
            {"entry_id": entry_id},
        )
        return list(rows.all())


async def _upload_for_patient(
    app: FastAPI,
    client: httpx.AsyncClient,
    care: DiaryCare,
    storage: MemoryStorage,
) -> str:
    app.state.storage = storage
    await login(client, care.patient.email)
    uploaded = await upload_voice(client, voice_metadata())
    assert uploaded.status_code == 201
    return uploaded.json()["entry_id"]


async def test_patient_can_stream_range_and_full_voice_with_committed_audits(
    app: FastAPI,
    client: httpx.AsyncClient,
    diary_care: DiaryCare,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    storage = MemoryStorage()
    app.state.storage = storage
    await login(client, diary_care.patient.email)
    uploaded = await upload_voice(client, voice_metadata())
    assert uploaded.status_code == 201
    entry_id = uploaded.json()["entry_id"]
    path = f"/api/v1/diary/entries/{entry_id}/audio"

    partial = await client.get(path, headers={"Range": "bytes=1-3"})
    complete = await client.get(path)

    assert partial.status_code == 206
    assert partial.content == WEBM[1:4]
    assert partial.headers["content-range"] == f"bytes 1-3/{len(WEBM)}"
    assert partial.headers["content-length"] == "3"
    assert partial.headers["accept-ranges"] == "bytes"
    assert partial.headers["cache-control"] == "no-store"
    assert partial.headers["content-type"] == "audio/webm"
    assert complete.status_code == 200
    assert complete.content == WEBM
    assert complete.headers["cache-control"] == "no-store"
    assert len(storage.get_calls) == 2
    assert all(call[2].closed for call in storage.get_calls)
    events = await _audio_events(sessionmaker, entry_id)
    assert [row[0] for row in events].count("diary.audio.read") == 2


async def test_unsatisfiable_range_is_audited_without_reading_storage(
    app: FastAPI,
    client: httpx.AsyncClient,
    diary_care: DiaryCare,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    storage = MemoryStorage()
    app.state.storage = storage
    entry_id = await _upload_for_patient(app, client, diary_care, storage)

    response = await client.get(
        f"/api/v1/diary/entries/{entry_id}/audio",
        headers={"Range": f"bytes={len(WEBM)}-"},
    )

    assert response.status_code == 416
    assert response.headers["content-range"] == f"bytes */{len(WEBM)}"
    assert response.headers["cache-control"] == "no-store"
    assert storage.get_calls == []
    assert [row[0] for row in await _audio_events(sessionmaker, entry_id)].count(
        "diary.audio.read"
    ) == 1


async def test_current_doctor_can_play_audio_and_foreign_doctor_is_audited_denied(
    app: FastAPI,
    client: httpx.AsyncClient,
    diary_care: DiaryCare,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    storage = MemoryStorage()
    app.state.storage = storage
    entry_id = await _upload_for_patient(app, client, diary_care, storage)
    foreign = await create_care(
        sessionmaker, "voice-foreign-doctor@example.com", "voice-foreign-patient@example.com"
    )
    path = f"/api/v1/patients/{diary_care.patient.id}/diary/{entry_id}/audio"

    await login(client, diary_care.doctor.email)
    allowed = await client.get(path)
    await login(client, foreign.doctor.email)
    denied = await client.get(path)

    assert allowed.status_code == 200
    assert allowed.content == WEBM
    assert denied.status_code == 404
    assert len(storage.get_calls) == 1
    events = await _audio_events(sessionmaker, entry_id)
    assert any(
        row[0] == "diary.audio.read" and row[1] == str(diary_care.patient.id) for row in events
    )
    assert any(
        row[0] == "diary.audio.read.denied" and row[1] == str(diary_care.patient.id)
        for row in events
    )


async def test_patient_cannot_play_foreign_or_hidden_entry_and_denial_survives_rollback(
    app: FastAPI,
    client: httpx.AsyncClient,
    diary_care: DiaryCare,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    storage = MemoryStorage()
    app.state.storage = storage
    entry_id = await _upload_for_patient(app, client, diary_care, storage)
    other = await create_care(
        sessionmaker, "voice-other-doctor@example.com", "voice-other-patient@example.com"
    )
    await login(client, other.patient.email)
    foreign = await client.get(f"/api/v1/diary/entries/{entry_id}/audio")
    assert foreign.status_code == 404
    assert storage.get_calls == []

    await login(client, diary_care.patient.email)
    hidden = await client.post(
        f"/api/v1/diary/entries/{entry_id}/hide",
        json={"operation_id": str(uuid4()), "expected_revision": 1},
    )
    assert hidden.status_code == 200
    response = await client.get(f"/api/v1/diary/entries/{entry_id}/audio")

    assert response.status_code == 404
    assert storage.get_calls == []
    events = await _audio_events(sessionmaker, entry_id)
    assert sum(row[0] == "diary.audio.read.denied" for row in events) == 2


async def test_non_clinical_role_is_rejected_and_missing_storage_is_masked(
    app: FastAPI,
    client: httpx.AsyncClient,
    diary_care: DiaryCare,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    storage = MemoryStorage()
    app.state.storage = storage
    entry_id = await _upload_for_patient(app, client, diary_care, storage)
    async with sessionmaker() as session:
        admin = await identity.create_user(
            session,
            email="voice-audio-admin@example.com",
            password=PASSWORD,
            role=Role.ADMIN,
            full_name="Администратор",
        )
        await session.commit()
    await login(client, admin.email)

    forbidden = await client.get(f"/api/v1/diary/entries/{entry_id}/audio")

    assert forbidden.status_code == 403
    assert any(
        row[0] == "diary.audio.read.denied" for row in await _audio_events(sessionmaker, entry_id)
    )
    await login(client, diary_care.patient.email)
    key = storage.put_calls[0][0]
    storage.objects.pop(key)
    unavailable = await client.get(f"/api/v1/diary/entries/{entry_id}/audio")

    assert unavailable.status_code == 503
    assert unavailable.json()["code"] == "storage_unavailable"
    assert key not in unavailable.text
    assert len(storage.get_calls) == 0
