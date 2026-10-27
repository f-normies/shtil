"""Лимиты, отказоустойчивость и уборка закрытых объектов voice-записей."""

import json

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.modules.diary import voice
from clin_compass.modules.diary.errors import DiaryError
from clin_compass.modules.diary.models import Audio, Entry, Operation
from tests.diary.conftest import DiaryCare, login
from tests.diary.voice_support import MP4, WEBM, MemoryStorage, upload_voice, voice_metadata

MAX_AUDIO_BYTES = 25 * 1024 * 1024
MAX_MULTIPART_BYTES = 26 * 1024 * 1024


async def _persisted_voice_count(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> tuple[int, int, int, int]:
    async with sessionmaker() as session:
        return (
            await session.scalar(select(text("count(*)")).select_from(Entry)) or 0,
            await session.scalar(select(text("count(*)")).select_from(Audio)) or 0,
            await session.scalar(select(text("count(*)")).select_from(Operation)) or 0,
            await session.scalar(text("SELECT count(*) FROM pgq.pgqueuer")) or 0,
        )


@pytest.mark.parametrize(
    ("audio", "content_type", "expected_status"),
    [
        (b"", "audio/webm", 422),
        (b"not a WebM recording", "audio/webm", 422),
        (WEBM, "audio/mpeg", 415),
        (MP4, "audio/mp4", 201),
    ],
)
async def test_upload_validates_container_and_supported_mime_before_storage(
    app: FastAPI,
    client: httpx.AsyncClient,
    diary_care: DiaryCare,
    sessionmaker: async_sessionmaker[AsyncSession],
    audio: bytes,
    content_type: str,
    expected_status: int,
) -> None:
    storage = MemoryStorage()
    app.state.storage = storage
    await login(client, diary_care.patient.email)

    response = await upload_voice(client, voice_metadata(), audio=audio, content_type=content_type)

    assert response.status_code == expected_status
    if expected_status == 201:
        assert storage.put_calls[0][1:] == (MP4, "audio/mp4")
    else:
        assert storage.put_calls == []
        assert await _persisted_voice_count(sessionmaker) == (0, 0, 0, 0)


async def test_doctor_cannot_upload_patient_audio(
    app: FastAPI,
    client: httpx.AsyncClient,
    diary_care: DiaryCare,
) -> None:
    storage = MemoryStorage()
    app.state.storage = storage
    await login(client, diary_care.doctor.email)

    response = await upload_voice(client, voice_metadata())

    assert response.status_code == 403
    assert storage.put_calls == []


async def test_actual_file_limit_is_enforced_before_storage(
    app: FastAPI,
    client: httpx.AsyncClient,
    diary_care: DiaryCare,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    storage = MemoryStorage()
    app.state.storage = storage
    await login(client, diary_care.patient.email)
    oversized = b"\x1a\x45\xdf\xa3" + b"x" * (MAX_AUDIO_BYTES - 3)
    assert len(oversized) == MAX_AUDIO_BYTES + 1

    response = await upload_voice(client, voice_metadata(), audio=oversized)

    assert response.status_code == 413
    assert response.json()["code"] == "audio_too_large"
    assert storage.put_calls == []
    assert await _persisted_voice_count(sessionmaker) == (0, 0, 0, 0)


async def test_actual_multipart_limit_is_enforced_independently_of_file_size(
    app: FastAPI,
    client: httpx.AsyncClient,
    diary_care: DiaryCare,
) -> None:
    storage = MemoryStorage()
    app.state.storage = storage
    await login(client, diary_care.patient.email)
    files = [
        ("metadata", (None, json.dumps(voice_metadata()), "application/json")),
        ("file", ("recording.webm", WEBM, "audio/webm")),
        ("padding", (None, "x" * MAX_MULTIPART_BYTES, "text/plain")),
    ]

    response = await client.post("/api/v1/diary/entries/voice", files=files)

    assert response.status_code == 413
    assert response.json()["code"] == "audio_too_large"
    assert storage.put_calls == []


async def test_upload_reads_actual_body_when_content_length_header_is_false(
    app: FastAPI,
    client: httpx.AsyncClient,
    diary_care: DiaryCare,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    storage = MemoryStorage()
    app.state.storage = storage
    await login(client, diary_care.patient.email)
    metadata = voice_metadata()

    response = await client.post(
        "/api/v1/diary/entries/voice",
        files={
            "metadata": (None, json.dumps(metadata), "application/json"),
            "file": ("../../patient.webm", WEBM, "audio/webm"),
        },
        headers={"content-length": "1"},
    )

    assert response.status_code == 201
    async with sessionmaker() as session:
        audio = await session.scalar(select(Audio).where(Audio.entry_id == metadata["entry_id"]))
    assert audio is not None and audio.byte_size == len(WEBM)
    assert storage.put_calls[0][1] == WEBM
    assert "patient.webm" not in response.text


async def test_known_business_rollback_deletes_only_its_uploaded_object(
    app: FastAPI,
    client: httpx.AsyncClient,
    diary_care: DiaryCare,
    sessionmaker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = MemoryStorage()
    app.state.storage = storage
    await login(client, diary_care.patient.email)

    async def reject_after_upload(*_args: object, **_kwargs: object) -> object:
        raise DiaryError(409, "synthetic_conflict", "Synthetic conflict")

    monkeypatch.setattr(voice.service, "create_voice_entry", reject_after_upload)
    response = await upload_voice(client, voice_metadata())

    assert response.status_code == 409
    assert len(storage.put_calls) == 1
    key = storage.put_calls[0][0]
    assert storage.delete_calls == [key]
    assert key not in storage.objects
    assert await _persisted_voice_count(sessionmaker) == (0, 0, 0, 0)


async def test_commit_ack_loss_is_resolved_without_deleting_accepted_audio(
    app: FastAPI,
    client: httpx.AsyncClient,
    diary_care: DiaryCare,
    sessionmaker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = MemoryStorage()
    app.state.storage = storage
    await login(client, diary_care.patient.email)
    original_commit = AsyncSession.commit
    raised = False

    async def commit_then_lose_ack(session: AsyncSession) -> None:
        nonlocal raised
        await original_commit(session)
        if not raised:
            raised = True
            raise SQLAlchemyError("synthetic commit acknowledgement loss")

    monkeypatch.setattr(AsyncSession, "commit", commit_then_lose_ack)
    response = await upload_voice(client, voice_metadata())

    assert response.status_code == 201
    assert len(storage.put_calls) == 1
    key = storage.put_calls[0][0]
    assert key in storage.objects
    assert storage.delete_calls == []
    assert await _persisted_voice_count(sessionmaker) == (1, 1, 1, 1)


async def test_unresolved_commit_preserves_uploaded_object_for_reconciliation(
    app: FastAPI,
    client: httpx.AsyncClient,
    diary_care: DiaryCare,
    sessionmaker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = MemoryStorage()
    app.state.storage = storage
    await login(client, diary_care.patient.email)
    original_commit = AsyncSession.commit
    raised = False

    async def fail_before_commit(session: AsyncSession) -> None:
        nonlocal raised
        if not raised:
            raised = True
            raise SQLAlchemyError("synthetic unknown commit outcome")
        await original_commit(session)

    monkeypatch.setattr(AsyncSession, "commit", fail_before_commit)
    response = await upload_voice(client, voice_metadata())

    assert response.status_code == 503
    assert response.json()["code"] == "diary_unavailable"
    assert len(storage.put_calls) == 1
    key = storage.put_calls[0][0]
    assert key in storage.objects
    assert storage.delete_calls == []
    assert await _persisted_voice_count(sessionmaker) == (0, 0, 0, 0)
