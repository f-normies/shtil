"""Проверки постановки и выполнения фоновой расшифровки дневника."""

from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.core.ai.runs import AiRun
from clin_compass.core.config import Settings
from clin_compass.core.queue import Entrypoint, RetryRequested
from clin_compass.modules.diary import service as diary_service
from clin_compass.modules.diary.models import Audio, Entry, Revision
from clin_compass.modules.diary.schemas import DiaryEntryPatchIn, DiaryHideIn
from clin_compass.modules.freetext.models import Analysis
from clin_compass.worker import build_handlers
from tests.core.fake_nodes import fake_node
from tests.diary.conftest import DiaryCare, login
from tests.diary.voice_support import WEBM, MemoryStorage, upload_voice, voice_metadata

TRANSCRIPT = "Сегодня было немного спокойнее."


async def _upload(
    app: FastAPI,
    client: httpx.AsyncClient,
    care: DiaryCare,
    storage: MemoryStorage,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> Audio:
    app.state.storage = storage
    await login(client, care.patient.email)
    response = await upload_voice(client, voice_metadata())
    assert response.status_code == 201
    async with sessionmaker() as session:
        audio = await session.scalar(select(Audio))
    assert audio is not None
    return audio


def _handlers(settings: Settings, sessionmaker: async_sessionmaker[AsyncSession]):
    handlers = build_handlers(settings, sessionmaker)
    handler = handlers.get("diary.transcribe")
    assert isinstance(handler, Entrypoint)
    assert handler.concurrency_limit == 1
    return handler.handler


def test_diary_transcription_entrypoint_is_registered(
    settings: Settings, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    handler = build_handlers(settings, sessionmaker).get("diary.transcribe")

    assert isinstance(handler, Entrypoint)
    assert handler.concurrency_limit == 1


async def test_transcript_and_freetext_are_saved_once_for_duplicate_delivery(
    app: FastAPI,
    client: httpx.AsyncClient,
    diary_care: DiaryCare,
    settings: Settings,
    sessionmaker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from clin_compass.core import storage as storage_module

    storage = MemoryStorage()
    audio = await _upload(app, client, diary_care, storage, sessionmaker)
    monkeypatch.setattr(storage_module, "build_storage", lambda _settings: storage)

    async with fake_node(lambda _: (200, {"text": TRANSCRIPT, "duration": 4.2})) as node:
        configured = settings.model_copy(update={"asr_urls": (node.url,)})
        handler = _handlers(configured, sessionmaker)
        payload = str(audio.id).encode()
        await handler(payload)
        await handler(payload)

    async with sessionmaker() as session:
        saved = await session.get(Audio, audio.id)
        assert saved is not None
        entry = await session.get(Entry, audio.entry_id)
        analysis = await session.scalar(
            select(Analysis).where(
                Analysis.source_type == "diary",
                Analysis.source_id == saved.transcript_source_id,
            )
        )
        runs = list(
            (await session.scalars(select(AiRun).where(AiRun.task == "diary.transcribe"))).all()
        )
        jobs = list((await session.scalars(text("SELECT entrypoint FROM pgq.pgqueuer"))).all())

    assert (saved.status, saved.transcript, saved.asr_run_id) == ("done", TRANSCRIPT, runs[0].id)
    assert saved.transcript_source_id is not None
    assert entry is not None and entry.hidden_at is None and entry.current_revision == 1
    assert analysis is not None
    assert (analysis.text, analysis.transcript, analysis.patient_id) == (
        TRANSCRIPT,
        True,
        diary_care.patient.id,
    )
    assert len(node.requests) == len(runs) == 1
    assert b'filename="diary.webm"' in node.requests[0].body
    assert WEBM in storage.objects[audio.storage_key][0]
    assert jobs.count("diary.transcribe") == 1
    assert jobs.count("freetext.check_risk") == 1
    assert jobs.count("freetext.extract") == 1


async def test_unconfigured_asr_waits_without_counting_a_failure(
    app: FastAPI,
    client: httpx.AsyncClient,
    diary_care: DiaryCare,
    settings: Settings,
    sessionmaker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from clin_compass.core import storage as storage_module

    storage = MemoryStorage()
    audio = await _upload(app, client, diary_care, storage, sessionmaker)
    monkeypatch.setattr(storage_module, "build_storage", lambda _settings: storage)

    with pytest.raises(RetryRequested) as retry:
        await _handlers(settings, sessionmaker)(str(audio.id).encode())

    assert retry.value.delay.total_seconds() == 30
    async with sessionmaker() as session:
        current = await session.get(Audio, audio.id)
    assert current is not None
    assert (current.status, current.failures) == ("pending", 0)


async def test_transcriber_rejects_stored_audio_with_an_unsupported_track_before_asr(
    app: FastAPI,
    client: httpx.AsyncClient,
    diary_care: DiaryCare,
    settings: Settings,
    sessionmaker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from clin_compass.core import storage as storage_module

    storage = MemoryStorage()
    audio = await _upload(app, client, diary_care, storage, sessionmaker)
    storage.objects[audio.storage_key] = (WEBM.replace(b"A_OPUS", b"A_FLAC"), "audio/webm")
    monkeypatch.setattr(storage_module, "build_storage", lambda _settings: storage)

    async with fake_node(lambda _: (200, {"text": TRANSCRIPT, "duration": 4.2})) as node:
        configured = settings.model_copy(update={"asr_urls": (node.url,)})
        with pytest.raises(RetryRequested):
            await _handlers(configured, sessionmaker)(str(audio.id).encode())

    async with sessionmaker() as session:
        current = await session.get(Audio, audio.id)
    assert current is not None and (current.status, current.failures) == ("processing", 1)
    assert node.requests == []


@pytest.mark.parametrize("transcript", ["", "x" * 20_001])
async def test_empty_or_long_transcript_fails_without_truncating_or_submitting(
    app: FastAPI,
    client: httpx.AsyncClient,
    diary_care: DiaryCare,
    settings: Settings,
    sessionmaker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    transcript: str,
) -> None:
    from clin_compass.core import storage as storage_module

    storage = MemoryStorage()
    audio = await _upload(app, client, diary_care, storage, sessionmaker)
    monkeypatch.setattr(storage_module, "build_storage", lambda _settings: storage)

    async with fake_node(lambda _: (200, {"text": transcript, "duration": 4.2})) as node:
        configured = settings.model_copy(update={"asr_urls": (node.url,)})
        await _handlers(configured, sessionmaker)(str(audio.id).encode())

    async with sessionmaker() as session:
        current = await session.get(Audio, audio.id)
        analysis = await session.scalar(
            select(Analysis).where(
                Analysis.source_type == "diary", Analysis.patient_id == diary_care.patient.id
            )
        )
        jobs = list(
            (
                await session.scalars(
                    text("SELECT entrypoint FROM pgq.pgqueuer WHERE entrypoint LIKE 'freetext.%'")
                )
            ).all()
        )
    assert current is not None
    assert (current.status, current.transcript, current.transcript_source_id) == (
        "failed",
        transcript,
        None,
    )
    assert analysis is None
    assert jobs == []


async def test_late_asr_keeps_manual_revision_and_saves_original_source(
    app: FastAPI,
    client: httpx.AsyncClient,
    diary_care: DiaryCare,
    settings: Settings,
    sessionmaker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from clin_compass.core import storage as storage_module

    storage = MemoryStorage()
    audio = await _upload(app, client, diary_care, storage, sessionmaker)
    monkeypatch.setattr(storage_module, "build_storage", lambda _settings: storage)

    async def edit_before_asr_returns(_request):
        async with sessionmaker() as session:
            await diary_service.patch_entry(
                session,
                diary_care.patient,
                audio.entry_id,
                DiaryEntryPatchIn(
                    operation_id=uuid4(),
                    expected_revision=1,
                    text="Ручная запись пациента.",
                ),
            )
            await session.commit()
        return 200, {"text": TRANSCRIPT, "duration": 4.2}

    async with fake_node(edit_before_asr_returns) as node:
        configured = settings.model_copy(update={"asr_urls": (node.url,)})
        await _handlers(configured, sessionmaker)(str(audio.id).encode())

    async with sessionmaker() as session:
        current = await session.get(Audio, audio.id)
        entry = await session.get(Entry, audio.entry_id)
        assert current is not None and entry is not None
        revision = await session.scalar(
            select(Revision).where(
                Revision.entry_id == audio.entry_id,
                Revision.revision == entry.current_revision,
            )
        )
        analysis = await session.scalar(
            select(Analysis).where(
                Analysis.source_type == "diary",
                Analysis.source_id == current.transcript_source_id,
            )
        )
    assert current is not None and current.status == "done"
    assert entry is not None and entry.current_revision == 2 and entry.hidden_at is None
    assert revision is not None and revision.text == "Ручная запись пациента."
    assert analysis is not None and analysis.text == TRANSCRIPT and analysis.transcript is True


async def test_late_asr_finishes_for_hidden_entry_without_resurrecting_it(
    app: FastAPI,
    client: httpx.AsyncClient,
    diary_care: DiaryCare,
    settings: Settings,
    sessionmaker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from clin_compass.core import storage as storage_module

    storage = MemoryStorage()
    audio = await _upload(app, client, diary_care, storage, sessionmaker)
    monkeypatch.setattr(storage_module, "build_storage", lambda _settings: storage)

    async def hide_before_asr_returns(_request):
        async with sessionmaker() as session:
            await diary_service.hide_entry(
                session,
                diary_care.patient,
                audio.entry_id,
                DiaryHideIn(operation_id=uuid4(), expected_revision=1),
            )
            await session.commit()
        return 200, {"text": TRANSCRIPT, "duration": 4.2}

    async with fake_node(hide_before_asr_returns) as node:
        configured = settings.model_copy(update={"asr_urls": (node.url,)})
        await _handlers(configured, sessionmaker)(str(audio.id).encode())

    hidden_response = await client.get(f"/api/v1/diary/entries/{audio.entry_id}")
    async with sessionmaker() as session:
        current = await session.get(Audio, audio.id)
        entry = await session.get(Entry, audio.entry_id)
        assert current is not None
        analysis = await session.scalar(
            select(Analysis).where(
                Analysis.source_type == "diary",
                Analysis.source_id == current.transcript_source_id,
            )
        )
    assert hidden_response.status_code == 404
    assert current is not None and current.status == "done" and current.transcript == TRANSCRIPT
    assert entry is not None and entry.hidden_at is not None and entry.current_revision == 1
    assert analysis is not None and analysis.text == TRANSCRIPT
