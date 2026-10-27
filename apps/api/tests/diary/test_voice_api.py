"""HTTP-контракты голосовой записи дневника."""

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.modules.diary.models import Audio, Entry
from tests.diary.conftest import DiaryCare, login
from tests.diary.voice_support import MP4, WEBM, MemoryStorage, upload_voice, voice_metadata


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("audio", "content_type"),
    [(WEBM, "audio/webm; codecs=opus"), (MP4, "audio/mp4")],
    ids=["webm-opus", "mp4-aac"],
)
async def test_patient_can_upload_voice_entry(
    app: FastAPI,
    client: httpx.AsyncClient,
    diary_care: DiaryCare,
    sessionmaker: async_sessionmaker[AsyncSession],
    audio: bytes,
    content_type: str,
) -> None:
    storage = MemoryStorage()
    app.state.storage = storage
    await login(client, diary_care.patient.email)
    metadata = voice_metadata()

    response = await upload_voice(client, metadata, audio=audio, content_type=content_type)

    assert response.status_code == 201
    assert response.headers["cache-control"] == "no-store"
    assert len(storage.put_calls) == 1
    body = response.json()
    assert body["entry_id"] == metadata["entry_id"]
    assert "storage_key" not in body
    async with sessionmaker() as session:
        entry = await session.get(Entry, body["entry_id"])
        audio = await session.scalar(select(Audio).where(Audio.entry_id == body["entry_id"]))
        jobs = list((await session.scalars(text("SELECT entrypoint FROM pgq.pgqueuer"))).all())
    assert entry is not None and entry.kind == "voice"
    assert audio is not None and audio.status == "pending"
    assert jobs == ["diary.transcribe"]


def test_voice_upload_openapi_describes_multipart_and_both_receipts(app: FastAPI) -> None:
    document = app.openapi()
    operation = document["paths"]["/api/v1/diary/entries/voice"]["post"]

    request_body = operation["requestBody"]
    multipart = request_body["content"]["multipart/form-data"]
    schema = multipart["schema"]
    assert request_body["required"] is True
    assert schema["required"] == ["metadata", "file"]
    assert schema["properties"]["metadata"] == {
        "$ref": "#/components/schemas/DiaryVoiceUploadMetadataIn"
    }
    assert schema["properties"]["file"] == {"type": "string", "format": "binary"}
    assert multipart["encoding"]["metadata"]["contentType"] == "application/json"
    assert multipart["encoding"]["file"]["contentType"] == "audio/webm, audio/mp4"

    metadata = document["components"]["schemas"]["DiaryVoiceUploadMetadataIn"]
    assert {"operation_id", "entry_id", "kind", "occurred_at", "duration_ms"} <= set(
        metadata["properties"]
    )
    for status in ("200", "201"):
        assert operation["responses"][status]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/DiaryCommandReceipt"
        }


@pytest.mark.asyncio
async def test_voice_replay_skips_second_upload_and_conflicts_on_different_audio(
    app: FastAPI,
    client: httpx.AsyncClient,
    diary_care: DiaryCare,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    storage = MemoryStorage()
    app.state.storage = storage
    await login(client, diary_care.patient.email)
    metadata = voice_metadata()

    first = await upload_voice(client, metadata)
    replay = await upload_voice(client, metadata)
    conflict = await upload_voice(
        client, metadata, audio=MP4, content_type="audio/mp4", filename="other-name.mp4"
    )

    assert first.status_code == 201
    assert replay.status_code == 200
    assert replay.json() == first.json()
    assert conflict.status_code == 409
    assert len(storage.put_calls) == 1
    async with sessionmaker() as session:
        entries = list((await session.scalars(select(Entry))).all())
        audios = list((await session.scalars(select(Audio))).all())
        jobs = list((await session.scalars(text("SELECT entrypoint FROM pgq.pgqueuer"))).all())
    assert len(entries) == 1
    assert len(audios) == 1
    assert jobs == ["diary.transcribe"]
