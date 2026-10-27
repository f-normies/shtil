"""Приём голосового сообщения и безопасная связь сообщения с закрытым объектом."""

import hashlib
from uuid import UUID, uuid4

import anyio
import structlog
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.core import queue, realtime
from clin_compass.core.storage import S3Storage, StorageError
from clin_compass.modules.freetext import service as freetext
from clin_compass.modules.identity.schemas import UserOut
from clin_compass.modules.messaging import service
from clin_compass.modules.messaging.models import Conversation, Message
from clin_compass.modules.messaging.schemas import MessageOut
from clin_compass.modules.messaging.views import message_out

log = structlog.get_logger()


class VoiceCommitUnknownError(Exception):
    """Ответ COMMIT потерян, а проверка новым соединением не доказала его исход."""


async def accept_voice(
    session: AsyncSession,
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: S3Storage,
    actor: UserOut,
    *,
    patient_id: UUID,
    expected_doctor_id: UUID,
    client_message_id: UUID,
    data: bytes,
    content_type: str,
) -> tuple[MessageOut, bool]:
    """Сохранить объект до транзакции сообщения и постановки worker-задачи."""
    digest = hashlib.sha256(data).hexdigest()
    doctor_id = await service._lock_current_pair(
        session, actor, patient_id, expected_doctor_id=expected_doctor_id
    )
    await service._lock_idempotency_key(session, actor.id, client_message_id)
    existing = await service._find_by_client_id(session, actor.id, client_message_id)
    if existing is not None:
        message, conversation = existing
        result = await _existing_message(
            session,
            message,
            conversation,
            patient_id=patient_id,
            doctor_id=doctor_id,
            digest=digest,
            content_type=content_type,
        )
        await session.commit()
        return result, False

    # Сетевую операцию нельзя выполнять, удерживая строковые блокировки Postgres.
    await session.rollback()
    object_key = f"messages/{uuid4()}"
    await anyio.to_thread.run_sync(storage.put, object_key, data, content_type)

    try:
        doctor_id = await service._lock_current_pair(
            session, actor, patient_id, expected_doctor_id=expected_doctor_id
        )
        await service._lock_idempotency_key(session, actor.id, client_message_id)
        existing = await service._find_by_client_id(session, actor.id, client_message_id)
        if existing is not None:
            message, conversation = existing
            result = await _existing_message(
                session,
                message,
                conversation,
                patient_id=patient_id,
                doctor_id=doctor_id,
                digest=digest,
                content_type=content_type,
            )
            await session.rollback()
            await _delete_attempt(storage, object_key)
            return result, False

        current_conversation = await service._get_conversation(
            session, patient_id, doctor_id, lock=True
        )
        if current_conversation is None:
            current_conversation = Conversation(patient_id=patient_id, doctor_id=doctor_id)
            session.add(current_conversation)
            await session.flush()
        message = Message(
            conversation_id=current_conversation.id,
            seq=current_conversation.last_seq + 1,
            sender_id=actor.id,
            client_message_id=client_message_id,
            kind="voice",
            audio_key=object_key,
            audio_mime=content_type,
            audio_sha256=digest,
            audio_size=len(data),
            transcription_status="queued",
        )
        current_conversation.last_seq = message.seq
        session.add(message)
        await session.flush()
        await session.refresh(message)
        await queue.enqueue(
            session,
            "messaging.transcribe",
            str(message.id).encode(),
            dedupe_key=f"messaging.transcribe:{message.id}",
        )
        await service.clear_typing(
            session, user_id=actor.id, patient_id=patient_id, doctor_id=doctor_id
        )
        await realtime.publish(
            session,
            [patient_id, doctor_id],
            "message.created",
            {
                "patient_id": str(patient_id),
                "doctor_id": str(doctor_id),
                "message_id": str(message.id),
                "seq": message.seq,
            },
        )
        statuses = await freetext.statuses(session, source_type="message", source_ids=[message.id])
        result = message_out(message, current_conversation, statuses)
    except BaseException:
        # До COMMIT строки сообщения никто не видит, поэтому объект своей попытки удаляем
        # при любой ошибке: asyncpg из pgqueuer минует SQLAlchemy, отмена — тоже исключение.
        with anyio.CancelScope(shield=True):
            try:
                await session.rollback()
            finally:
                await _delete_attempt(storage, object_key)
        raise

    try:
        await session.commit()
    except SQLAlchemyError:
        await session.rollback()
        resolved = await _resolve_commit(
            sessionmaker,
            storage,
            object_key=object_key,
            sender_id=actor.id,
            patient_id=patient_id,
            doctor_id=doctor_id,
            client_message_id=client_message_id,
            digest=digest,
            content_type=content_type,
        )
        if resolved is None:
            # Удаление небезопасно: пустая проверка не доказывает откат COMMIT.
            raise VoiceCommitUnknownError("message commit outcome is unknown") from None
        return resolved
    return result, True


async def _existing_message(
    session: AsyncSession,
    message: Message,
    conversation: Conversation,
    *,
    patient_id: UUID,
    doctor_id: UUID,
    digest: str,
    content_type: str,
) -> MessageOut:
    if conversation.patient_id != patient_id or conversation.doctor_id != doctor_id:
        raise service.MessagingError(409, "conversation_changed", "Conversation changed")
    if (
        message.kind != "voice"
        or message.audio_sha256 != digest
        or message.audio_mime != content_type
    ):
        raise service.MessagingError(
            409, "idempotency_conflict", "Message key was used for other data"
        )
    statuses = await freetext.statuses(session, source_type="message", source_ids=[message.id])
    return message_out(message, conversation, statuses)


async def _delete_attempt(storage: S3Storage, key: str) -> None:
    try:
        await anyio.to_thread.run_sync(storage.delete, key)
    except StorageError as error:
        log.warning("messaging.voice_orphan_cleanup_failed", error_type=type(error).__name__)


async def _resolve_commit(
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: S3Storage,
    *,
    object_key: str,
    sender_id: UUID,
    patient_id: UUID,
    doctor_id: UUID,
    client_message_id: UUID,
    digest: str,
    content_type: str,
) -> tuple[MessageOut, bool] | None:
    result: MessageOut | None = None
    same_object = False
    conflict: service.MessagingError | None = None
    try:
        async with sessionmaker() as check_session:
            row = (
                await check_session.execute(
                    select(Message, Conversation)
                    .join(Conversation, Conversation.id == Message.conversation_id)
                    .where(
                        Message.sender_id == sender_id,
                        Message.client_message_id == client_message_id,
                    )
                )
            ).one_or_none()
            if row is None:
                return None
            message, conversation = row
            same_object = message.audio_key == object_key
            try:
                result = await _existing_message(
                    check_session,
                    message,
                    conversation,
                    patient_id=patient_id,
                    doctor_id=doctor_id,
                    digest=digest,
                    content_type=content_type,
                )
            except service.MessagingError as error:
                conflict = error
    except SQLAlchemyError:
        return None

    if not same_object:
        await _delete_attempt(storage, object_key)
    if conflict is not None:
        raise conflict
    if result is None:
        return None
    return result, same_object
