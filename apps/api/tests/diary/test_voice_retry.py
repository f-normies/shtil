"""Идемпотентный повтор терминальной ошибки расшифровки."""

from uuid import uuid4

import httpx
from fastapi import FastAPI
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.modules.diary.models import Audio
from tests.diary.conftest import DiaryCare, login
from tests.diary.voice_support import MemoryStorage, upload_voice, voice_metadata


async def test_only_terminal_voice_failure_can_be_retried_once(
    app: FastAPI,
    client: httpx.AsyncClient,
    diary_care: DiaryCare,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    storage = MemoryStorage()
    app.state.storage = storage
    await login(client, diary_care.patient.email)
    response = await upload_voice(client, voice_metadata())
    assert response.status_code == 201
    entry_id = response.json()["entry_id"]
    async with sessionmaker() as session:
        audio = await session.scalar(select(Audio).where(Audio.entry_id == entry_id))
        assert audio is not None
        audio.status = "failed"
        audio.failures = 2
        audio.transcript = ""
        await session.commit()

    operation_id = str(uuid4())
    path = f"/api/v1/diary/entries/{entry_id}/transcription/retry"
    first = await client.post(path, json={"operation_id": operation_id})
    replay = await client.post(path, json={"operation_id": operation_id})
    other_attempt = await client.post(path, json={"operation_id": str(uuid4())})

    assert first.status_code == 202
    assert first.json()["operation_id"] == operation_id
    assert first.json()["status"] == "pending"
    assert replay.status_code == 200
    assert replay.json() == first.json()
    assert other_attempt.status_code == 409
    async with sessionmaker() as session:
        current = await session.get(Audio, audio.id)
        jobs = list((await session.scalars(text("SELECT entrypoint FROM pgq.pgqueuer"))).all())
    assert current is not None
    assert (current.status, current.failures, current.transcript) == ("pending", 0, None)
    assert jobs.count("diary.transcribe") == 2
