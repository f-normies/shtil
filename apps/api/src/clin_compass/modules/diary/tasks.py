"""Фоновая ASR-обработка аудиозаписей дневника."""

from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID, uuid4

import anyio
import structlog
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.core.ai.asr import AsrRejectedError, build_asr_client
from clin_compass.core.ai.hotwords import drug_hotwords
from clin_compass.core.ai.nodes import AiNotConfiguredError, NodesUnavailableError
from clin_compass.core.ai.runs import DbRunRecorder
from clin_compass.core.config import Settings
from clin_compass.core.queue import Entrypoint, RetryRequested
from clin_compass.core.realtime import publish
from clin_compass.core.storage import (
    S3Storage,
    StorageError,
    StorageObjectMissingError,
    StoredObject,
)
from clin_compass.modules.diary.audio_format import is_supported_audio
from clin_compass.modules.diary.models import Audio, Entry
from clin_compass.modules.freetext import service as freetext
from clin_compass.modules.patients import service as patients

log = structlog.get_logger()

_ENTRYPOINT = "diary.transcribe"
_MAX_AUDIO_BYTES = 25 * 1024 * 1024
_TRANSCRIPT_LIMIT = freetext.MAX_TEXT_LENGTH
_UNAVAILABLE_DELAY = timedelta(seconds=30)
_FAILURE_DELAYS = {1: timedelta(seconds=5), 2: timedelta(seconds=20)}
_FAILURE_LIMIT = 3


@dataclass(frozen=True, slots=True)
class VoiceSnapshot:
    """Immutable storage fields read before contacting S3 and ASR."""

    audio_id: UUID
    entry_id: UUID
    patient_id: UUID
    storage_key: str
    mime_type: str
    byte_size: int
    status: str


class AudioPayloadInvalidError(Exception):
    """Stored object does not match its accepted audio metadata."""


@dataclass(frozen=True)
class Jobs:
    """ASR job dependencies; the worker never holds a database transaction over I/O."""

    sessionmaker: async_sessionmaker[AsyncSession]
    settings: Settings
    storage: S3Storage | None

    def handlers(self) -> dict[str, Entrypoint]:
        """Keep the entrypoint registered so unavailable ASR is retried by the queue."""
        return {_ENTRYPOINT: Entrypoint(self.transcribe, concurrency_limit=1)}

    async def transcribe(self, payload: bytes | None) -> None:
        """Transcribe accepted audio, then atomically save its source and freetext jobs."""
        audio_id = _payload_id(payload)
        if audio_id is None:
            return
        try:
            snapshot = await self._load_snapshot(audio_id)
        except SQLAlchemyError as error:
            _log_error("diary.transcribe.database_unavailable", error)
            raise _retry(_UNAVAILABLE_DELAY, "database unavailable") from None
        if snapshot is None or snapshot.status in {"done", "failed"}:
            return
        if not self.settings.asr_urls or self.storage is None:
            raise _retry(_UNAVAILABLE_DELAY, "voice dependency unavailable")

        snapshot = await self._mark_processing(audio_id)
        if snapshot is None:
            return
        try:
            audio = await anyio.to_thread.run_sync(
                _read_audio,
                self.storage,
                snapshot.storage_key,
                snapshot.byte_size,
                snapshot.mime_type,
            )
        except StorageObjectMissingError:
            await self._finish_failure(snapshot, reason="audio_missing")
            return
        except AudioPayloadInvalidError as error:
            await self._count_real_error(snapshot, error)
            return
        except (StorageError, OSError) as error:
            _log_error("diary.transcribe.storage_unavailable", error)
            raise _retry(_UNAVAILABLE_DELAY, "storage unavailable") from None

        try:
            result = await build_asr_client(
                self.settings, DbRunRecorder(self.sessionmaker)
            ).transcribe(
                audio,
                filename="diary.webm" if snapshot.mime_type == "audio/webm" else "diary.mp4",
                content_type=snapshot.mime_type,
                hotwords=drug_hotwords(self.settings.knowledge_dir),
                task=_ENTRYPOINT,
            )
        except AsrRejectedError:
            await self._finish_failure(snapshot, reason="asr_rejected")
            return
        except (AiNotConfiguredError, NodesUnavailableError) as error:
            _log_error("diary.transcribe.asr_unavailable", error)
            raise _retry(_UNAVAILABLE_DELAY, "asr unavailable") from None
        except SQLAlchemyError as error:
            _log_error("diary.transcribe.database_unavailable", error)
            raise _retry(_UNAVAILABLE_DELAY, "database unavailable") from None
        except Exception as error:  # noqa: BLE001 — ASR errors use a bounded, explicit retry count.
            await self._count_real_error(snapshot, error)
            return

        transcript = result.output.text
        duration_ms = max(0, round(result.output.duration * 1000))
        if not transcript.strip() or len(transcript) > _TRANSCRIPT_LIMIT:
            await self._finish_failure(
                snapshot,
                reason="invalid_transcript",
                transcript=transcript,
                run_id=result.run_id,
                duration_ms=duration_ms,
            )
            return
        try:
            await self._finish_success(
                snapshot, transcript=transcript, run_id=result.run_id, duration_ms=duration_ms
            )
        except SQLAlchemyError as error:
            _log_error("diary.transcribe.database_unavailable", error)
            raise _retry(_UNAVAILABLE_DELAY, "database unavailable") from None
        except Exception as error:  # noqa: BLE001 — preserve retry limits for persistence errors.
            await self._count_real_error(snapshot, error)

    async def _load_snapshot(self, audio_id: UUID) -> VoiceSnapshot | None:
        async with self.sessionmaker() as session:
            row = (
                await session.execute(
                    select(Audio, Entry)
                    .join(Entry, Entry.id == Audio.entry_id)
                    .where(Audio.id == audio_id)
                )
            ).one_or_none()
            return _snapshot(row[0], row[1]) if row is not None else None

    async def _mark_processing(self, audio_id: UUID) -> VoiceSnapshot | None:
        try:
            async with self.sessionmaker.begin() as session:
                row = await _locked_audio_entry(session, audio_id)
                if row is None:
                    return None
                audio, entry = row
                if audio.status in {"done", "failed"}:
                    return None
                changed = audio.status != "processing"
                audio.status = "processing"
                if changed:
                    await _publish_status(session, entry, audio.status)
                await session.flush()
                return _snapshot(audio, entry)
        except SQLAlchemyError as error:
            _log_error("diary.transcribe.database_unavailable", error)
            raise _retry(_UNAVAILABLE_DELAY, "database unavailable") from None

    async def _finish_success(
        self, snapshot: VoiceSnapshot, *, transcript: str, run_id: UUID, duration_ms: int
    ) -> None:
        async with self.sessionmaker.begin() as session:
            row = await _locked_audio_entry(session, snapshot.audio_id)
            if row is None:
                return
            audio, entry = row
            if audio.status in {"done", "failed"}:
                return
            source_id = audio.transcript_source_id or uuid4()
            audio.transcript = transcript
            audio.transcript_source_id = source_id
            audio.asr_run_id = run_id
            audio.duration_ms = duration_ms
            audio.status = "done"
            await freetext.submit_text(
                session,
                patient_id=snapshot.patient_id,
                source_type="diary",
                source_id=source_id,
                text=transcript,
                transcript=True,
            )
            await _publish_status(session, entry, audio.status)

    async def _finish_failure(
        self,
        snapshot: VoiceSnapshot,
        *,
        reason: str,
        transcript: str | None = None,
        run_id: UUID | None = None,
        duration_ms: int | None = None,
    ) -> None:
        async with self.sessionmaker.begin() as session:
            row = await _locked_audio_entry(session, snapshot.audio_id)
            if row is None:
                return
            audio, entry = row
            if audio.status in {"done", "failed"}:
                return
            audio.status = "failed"
            if transcript is not None:
                audio.transcript = transcript
            if run_id is not None:
                audio.asr_run_id = run_id
            if duration_ms is not None:
                audio.duration_ms = duration_ms
            await _publish_status(session, entry, audio.status)
        log.warning("diary.transcribe.terminal_failure", reason=reason)

    async def _count_real_error(self, snapshot: VoiceSnapshot, error: Exception) -> None:
        try:
            async with self.sessionmaker.begin() as session:
                row = await _locked_audio_entry(session, snapshot.audio_id)
                if row is None:
                    return
                audio, entry = row
                if audio.status in {"done", "failed"}:
                    return
                failures = int(audio.failures or 0) + 1
                audio.failures = failures
                if failures >= _FAILURE_LIMIT:
                    audio.status = "failed"
                    await _publish_status(session, entry, audio.status)
        except SQLAlchemyError as database_error:
            _log_error("diary.transcribe.database_unavailable", database_error)
            raise _retry(_UNAVAILABLE_DELAY, "database unavailable") from None

        _log_error("diary.transcribe.attempt_failed", error)
        if failures < _FAILURE_LIMIT:
            raise _retry(_FAILURE_DELAYS[failures], "transcription attempt failed") from None


def build_jobs(settings: Settings, sessionmaker: async_sessionmaker[AsyncSession]) -> Jobs:
    """Build a handler even when ASR or private storage is not configured."""
    from clin_compass.core.storage import build_storage

    return Jobs(sessionmaker, settings, build_storage(settings))


async def _locked_audio_entry(session: AsyncSession, audio_id: UUID) -> tuple[Audio, Entry] | None:
    result = await session.execute(
        select(Audio, Entry)
        .join(Entry, Entry.id == Audio.entry_id)
        .where(Audio.id == audio_id)
        .with_for_update(of=Audio)
    )
    return result.one_or_none()


def _snapshot(audio: Audio, entry: Entry) -> VoiceSnapshot | None:
    if (
        audio.byte_size <= 0
        or not audio.storage_key
        or audio.mime_type
        not in {
            "audio/webm",
            "audio/mp4",
        }
    ):
        return None
    return VoiceSnapshot(
        audio_id=audio.id,
        entry_id=entry.id,
        patient_id=entry.patient_id,
        storage_key=audio.storage_key,
        mime_type=audio.mime_type,
        byte_size=audio.byte_size,
        status=audio.status,
    )


def _read_audio(storage: S3Storage, key: str, expected_size: int, content_type: str) -> bytes:
    stored: StoredObject = storage.get(key)
    try:
        if stored.size != expected_size or not 0 < stored.size <= _MAX_AUDIO_BYTES:
            raise AudioPayloadInvalidError
        audio = stored.body.read(_MAX_AUDIO_BYTES + 1)
        if len(audio) != expected_size or not is_supported_audio(audio, content_type):
            raise AudioPayloadInvalidError
        return audio
    finally:
        stored.body.close()


def _payload_id(payload: bytes | None) -> UUID | None:
    if payload is None:
        return None
    try:
        return UUID(payload.decode("ascii"))
    except (UnicodeDecodeError, ValueError):
        log.warning("diary.transcribe.invalid_payload")
        return None


async def _publish_status(session: AsyncSession, entry: Entry, status: str) -> None:
    doctor_id = await patients.lead_doctor_of(session, entry.patient_id)
    audience = [entry.patient_id]
    if doctor_id is not None:
        audience.append(doctor_id)
    await publish(
        session,
        audience,
        "diary.changed",
        {"patient_id": str(entry.patient_id), "entry_id": str(entry.id), "audio_status": status},
    )


def _retry(delay: timedelta, reason: str) -> RetryRequested:
    return RetryRequested(delay=delay, reason=reason)


def _log_error(event: str, error: Exception) -> None:
    log.warning(event, error_type=type(error).__name__)
