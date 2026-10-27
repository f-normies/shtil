"""Фоновая расшифровка голосовых сообщений."""

from dataclasses import dataclass
from datetime import timedelta
from typing import Literal
from uuid import UUID

import anyio
import structlog
from botocore.exceptions import BotoCoreError
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
)
from clin_compass.modules.freetext import service as freetext
from clin_compass.modules.messaging.models import Conversation, Message
from clin_compass.modules.patients import service as patients

log = structlog.get_logger()

_ENTRYPOINT = "messaging.transcribe"
_CONCURRENCY = 1
_MAX_AUDIO_BYTES = 25 * 1024 * 1024
_UNAVAILABLE_DELAY = timedelta(seconds=30)
_TRANSCRIPT_LIMIT = freetext.MAX_TEXT_LENGTH
_FAILURE_DELAYS = {1: timedelta(seconds=5), 2: timedelta(seconds=20)}
_FAILURE_LIMIT = 3


@dataclass(frozen=True, slots=True)
class VoiceSnapshot:
    """Поля сообщения, нужные вне короткой транзакции БД."""

    id: UUID
    conversation_id: UUID
    patient_id: UUID
    audio_key: str
    audio_mime: str
    audio_size: int
    status: str


class AudioPayloadInvalidError(Exception):
    """Объект в S3 нарушает ограничения аудиосообщения."""


@dataclass(frozen=True)
class Jobs:
    """Обработчик с общими зависимостями клиента ASR и закрытого S3."""

    sessionmaker: async_sessionmaker[AsyncSession]
    settings: Settings
    storage: S3Storage | None

    def handlers(self) -> dict[str, Entrypoint]:
        """Зарегистрировать расшифровку даже при выключенных узлах ASR."""
        return {
            _ENTRYPOINT: Entrypoint(self.transcribe, concurrency_limit=_CONCURRENCY),
        }

    async def transcribe(self, payload: bytes | None) -> None:
        """Расшифровать сообщение, сохраняя внешние вызовы вне транзакции."""
        message_id = _payload_id(payload)
        if message_id is None:
            return

        try:
            snapshot = await self._load_snapshot(message_id)
        except SQLAlchemyError as error:
            _log_error("messaging.transcribe.database_unavailable", error)
            raise _retry(_UNAVAILABLE_DELAY, "database unavailable") from None
        if snapshot is None or snapshot.status in {"done", "failed"}:
            return
        if not self.settings.asr_urls or self.storage is None:
            raise _retry(_UNAVAILABLE_DELAY, "voice dependency unavailable")

        snapshot = await self._mark_processing(snapshot)
        if snapshot is None:
            return

        try:
            audio = await anyio.to_thread.run_sync(
                _read_audio, self.storage, snapshot.audio_key, snapshot.audio_size
            )
        except StorageObjectMissingError:
            await self._mark_terminal_failure(snapshot.id, "audio_missing")
            return
        except (StorageError, AudioPayloadInvalidError) as error:
            if isinstance(error, AudioPayloadInvalidError):
                await self._count_real_error(snapshot.id, error)
            else:
                _log_error("messaging.transcribe.storage_unavailable", error)
                raise _retry(_UNAVAILABLE_DELAY, "storage unavailable") from None
            return
        except (BotoCoreError, OSError) as error:
            # Потоковое чтение S3 может завершиться сетевой ошибкой после GetObject.
            _log_error("messaging.transcribe.storage_unavailable", error)
            raise _retry(_UNAVAILABLE_DELAY, "storage unavailable") from None

        try:
            result = await build_asr_client(
                self.settings, DbRunRecorder(self.sessionmaker)
            ).transcribe(
                audio,
                filename="message.webm" if snapshot.audio_mime == "audio/webm" else "message.mp4",
                content_type=snapshot.audio_mime,
                hotwords=drug_hotwords(self.settings.knowledge_dir),
                task=_ENTRYPOINT,
            )
        except AsrRejectedError:
            await self._mark_terminal_failure(snapshot.id, "asr_rejected")
            return
        except (AiNotConfiguredError, NodesUnavailableError) as error:
            _log_error("messaging.transcribe.asr_unavailable", error)
            raise _retry(_UNAVAILABLE_DELAY, "asr unavailable") from None
        except SQLAlchemyError as error:
            _log_error("messaging.transcribe.database_unavailable", error)
            raise _retry(_UNAVAILABLE_DELAY, "database unavailable") from None
        except Exception as error:  # noqa: BLE001 — все реальные ошибки ASR считают ограниченные попытки.
            await self._count_real_error(snapshot.id, error)
            return

        try:
            await self._finish_success(
                snapshot.id,
                text=result.output.text,
                duration_ms=round(result.output.duration * 1000),
                run_id=result.run_id,
            )
        except SQLAlchemyError as error:
            _log_error("messaging.transcribe.database_unavailable", error)
            raise _retry(_UNAVAILABLE_DELAY, "database unavailable") from None
        except Exception as error:  # noqa: BLE001 — ошибки фиксации результата учитываются конечным счётчиком.
            await self._count_real_error(snapshot.id, error)

    async def _load_snapshot(self, message_id: UUID) -> VoiceSnapshot | None:
        async with self.sessionmaker() as session:
            row = (
                await session.execute(
                    select(Message, Conversation)
                    .join(Conversation, Conversation.id == Message.conversation_id)
                    .where(Message.id == message_id)
                )
            ).one_or_none()
            return _snapshot(row[0], row[1]) if row is not None else None

    async def _mark_processing(self, snapshot: VoiceSnapshot) -> VoiceSnapshot | None:
        try:
            async with self.sessionmaker.begin() as session:
                locked = await _lock_message(session, snapshot)
                if locked is None:
                    return None
                message, conversation, current_doctor = locked
                if message.transcription_status in {"done", "failed"}:
                    return None
                changed = message.transcription_status != "processing"
                message.transcription_status = "processing"
                if changed:
                    await _publish_update(session, message, conversation, current_doctor)
                await session.flush()
                return _snapshot(message, conversation)
        except SQLAlchemyError as error:
            _log_error("messaging.transcribe.database_unavailable", error)
            raise _retry(_UNAVAILABLE_DELAY, "database unavailable") from None

    async def _mark_terminal_failure(
        self, message_id: UUID, code: Literal["audio_missing", "asr_rejected"]
    ) -> None:
        try:
            snapshot = await self._load_snapshot(message_id)
            if snapshot is None:
                return
            async with self.sessionmaker.begin() as session:
                locked = await _lock_message(session, snapshot)
                if locked is None:
                    return
                message, conversation, current_doctor = locked
                if message.transcription_status in {"done", "failed"}:
                    return
                message.transcription_status = "failed"
                message.transcription_error_code = code
                await _publish_update(session, message, conversation, current_doctor)
        except SQLAlchemyError as error:
            _log_error("messaging.transcribe.database_unavailable", error)
            raise _retry(_UNAVAILABLE_DELAY, "database unavailable") from None

    async def _count_real_error(self, message_id: UUID, error: Exception) -> None:
        try:
            snapshot = await self._load_snapshot(message_id)
            if snapshot is None:
                return
            async with self.sessionmaker.begin() as session:
                locked = await _lock_message(session, snapshot)
                if locked is None:
                    return
                message, conversation, current_doctor = locked
                if message.transcription_status in {"done", "failed"}:
                    return
                failures = int(message.transcription_failures or 0) + 1
                message.transcription_failures = failures
                if failures >= _FAILURE_LIMIT:
                    message.transcription_status = "failed"
                    message.transcription_error_code = "transcription_failed"
                    await _publish_update(session, message, conversation, current_doctor)
        except SQLAlchemyError as database_error:
            _log_error("messaging.transcribe.database_unavailable", database_error)
            raise _retry(_UNAVAILABLE_DELAY, "database unavailable") from None

        _log_error("messaging.transcribe.attempt_failed", error)
        if failures < _FAILURE_LIMIT:
            delay = _FAILURE_DELAYS[failures]
            raise _retry(delay, "transcription attempt failed") from None

    async def _finish_success(
        self, message_id: UUID, *, text: str, duration_ms: int, run_id: UUID
    ) -> None:
        snapshot = await self._load_snapshot(message_id)
        if snapshot is None:
            return
        async with self.sessionmaker.begin() as session:
            locked = await _lock_message(session, snapshot)
            if locked is None:
                return
            message, conversation, current_doctor = locked
            if message.transcription_status in {"done", "failed"}:
                return
            message.transcription_text = text
            message.duration_ms = max(0, duration_ms)
            message.asr_run_id = run_id
            message.transcription_error_code = None

            if (
                message.sender_id == conversation.patient_id
                and text.strip()
                and len(text) <= _TRANSCRIPT_LIMIT
            ):
                await freetext.submit_text(
                    session,
                    patient_id=conversation.patient_id,
                    source_type="message",
                    source_id=message.id,
                    text=text,
                    transcript=True,
                    # Разбор ставится после расшифровки: время записи — время сообщения.
                    recorded_at=message.created_at,
                )

            message.transcription_status = "done"
            await _publish_update(session, message, conversation, current_doctor)


def build_jobs(settings: Settings, sessionmaker: async_sessionmaker[AsyncSession]) -> Jobs:
    """Создать handler независимо от доступности ASR или S3."""
    from clin_compass.core.storage import build_storage

    return Jobs(sessionmaker, settings, build_storage(settings))


async def _lock_message(
    session: AsyncSession, snapshot: VoiceSnapshot
) -> tuple[Message, Conversation, UUID | None] | None:
    """Блокировать в принятом порядке: пациент, диалог, сообщение."""
    current_doctor = await patients.lead_doctor_of_for_update(session, snapshot.patient_id)
    conversation = await session.scalar(
        select(Conversation).where(Conversation.id == snapshot.conversation_id).with_for_update()
    )
    message = await session.scalar(
        select(Message).where(Message.id == snapshot.id).with_for_update()
    )
    if (
        conversation is None
        or message is None
        or message.conversation_id != conversation.id
        or message.kind != "voice"
    ):
        return None
    return message, conversation, current_doctor


def _snapshot(message: Message, conversation: Conversation) -> VoiceSnapshot | None:
    if (
        message.kind != "voice"
        or message.audio_key is None
        or message.audio_mime is None
        or message.audio_size is None
    ):
        return None
    return VoiceSnapshot(
        id=message.id,
        conversation_id=conversation.id,
        patient_id=conversation.patient_id,
        audio_key=message.audio_key,
        audio_mime=message.audio_mime,
        audio_size=message.audio_size,
        status=message.transcription_status or "queued",
    )


def _payload_id(payload: bytes | None) -> UUID | None:
    if payload is None:
        return None
    try:
        return UUID(payload.decode("ascii"))
    except (UnicodeDecodeError, ValueError):
        log.warning("messaging.transcribe.invalid_payload")
        return None


def _read_audio(storage: S3Storage, key: str, expected_size: int) -> bytes:
    stored = storage.get(key)
    try:
        if stored.size > _MAX_AUDIO_BYTES or stored.size != expected_size:
            raise AudioPayloadInvalidError("stored audio size is invalid")
        audio = stored.body.read(_MAX_AUDIO_BYTES + 1)
        if len(audio) > _MAX_AUDIO_BYTES or len(audio) != expected_size:
            raise AudioPayloadInvalidError("stored audio size is invalid")
        return audio
    finally:
        stored.body.close()


async def _publish_update(
    session: AsyncSession,
    message: Message,
    conversation: Conversation,
    current_doctor: UUID | None,
) -> None:
    if current_doctor != conversation.doctor_id:
        return
    await publish(
        session,
        [conversation.patient_id, conversation.doctor_id],
        "message.updated",
        {
            "patient_id": str(conversation.patient_id),
            "doctor_id": str(conversation.doctor_id),
            "message_id": str(message.id),
            "seq": message.seq,
            "transcription_status": message.transcription_status,
        },
    )


def _retry(delay: timedelta, reason: str) -> RetryRequested:
    return RetryRequested(delay=delay, reason=reason)


def _log_error(event: str, error: Exception) -> None:
    log.warning(event, error_type=type(error).__name__)
