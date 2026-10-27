"""Публичная логика переписки: владение, история, отправка, прочтение и realtime-контракт.

Этот фасад немного длиннее 400 строк, чтобы все публичные операции одного модуля
оставались вместе; таблицы и SQL realtime-состояния вынесены в `presence.py`.
"""

import contextlib
from datetime import datetime
from uuid import UUID

import structlog
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from clin_compass.core import realtime
from clin_compass.modules.freetext import service as freetext
from clin_compass.modules.identity import service as identity
from clin_compass.modules.identity.schemas import Role, UserOut
from clin_compass.modules.messaging import presence
from clin_compass.modules.messaging.errors import MessagingError as MessagingError
from clin_compass.modules.messaging.models import Conversation, Message
from clin_compass.modules.messaging.schemas import (
    MessageCreateIn,
    MessageHistoryOut,
    MessageOut,
    MessagingStatusOut,
    ReadCreateIn,
    ReadOut,
    UnreadOut,
)
from clin_compass.modules.messaging.views import message_out
from clin_compass.modules.patients import service as patients

log = structlog.get_logger()


async def send_text(
    session: AsyncSession,
    actor: UserOut,
    patient_id: UUID,
    body: MessageCreateIn,
) -> tuple[MessageOut, bool]:
    """Принять текст и разбор пациента в одной транзакции; коммитит вызывающий."""
    doctor_id = await _lock_current_pair(
        session, actor, patient_id, expected_doctor_id=body.expected_doctor_id
    )
    await _lock_idempotency_key(session, actor.id, body.client_message_id)
    existing = await _find_by_client_id(session, actor.id, body.client_message_id)
    if existing is not None:
        existing_message, existing_conversation = existing
        if (
            existing_conversation.patient_id != patient_id
            or existing_conversation.doctor_id != doctor_id
        ):
            raise MessagingError(409, "conversation_changed", "Conversation changed")
        if existing_message.kind != "text" or existing_message.text != body.text:
            raise MessagingError(409, "idempotency_conflict", "Message key was used for other data")
        statuses = await freetext.statuses(
            session, source_type="message", source_ids=[existing_message.id]
        )
        return message_out(existing_message, existing_conversation, statuses), False

    conversation = await _get_conversation(session, patient_id, doctor_id, lock=True)
    if conversation is None:
        conversation = Conversation(patient_id=patient_id, doctor_id=doctor_id)
        session.add(conversation)
        await session.flush()

    message = Message(
        conversation_id=conversation.id,
        seq=conversation.last_seq + 1,
        sender_id=actor.id,
        client_message_id=body.client_message_id,
        kind="text",
        text=body.text,
    )
    conversation.last_seq = message.seq
    session.add(message)
    await session.flush()
    await session.refresh(message)

    if actor.role is Role.PATIENT:
        await freetext.submit_text(
            session,
            patient_id=patient_id,
            source_type="message",
            source_id=message.id,
            text=body.text,
            transcript=False,
        )
    await clear_typing(session, user_id=actor.id, patient_id=patient_id, doctor_id=doctor_id)
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
    return message_out(message, conversation, statuses), True


async def list_history(
    session: AsyncSession,
    actor: UserOut,
    patient_id: UUID,
    *,
    before_seq: int | None,
    after_seq: int | None,
    expected_doctor_id: UUID | None,
    limit: int,
) -> MessageHistoryOut:
    """Вернуть ограниченное окно текущей пары без изменения курсора прочтения."""
    if before_seq is not None and after_seq is not None:
        raise MessagingError(422, "invalid_cursor", "Only one history cursor may be used")
    if (before_seq is not None or after_seq is not None) and expected_doctor_id is None:
        raise MessagingError(422, "invalid_cursor", "expected_doctor_id is required with a cursor")
    if (before_seq is not None and before_seq < 0) or (after_seq is not None and after_seq < 0):
        raise MessagingError(422, "invalid_cursor", "History cursor must be non-negative")

    doctor_id = await _lock_current_pair(
        session, actor, patient_id, expected_doctor_id=expected_doctor_id
    )
    conversation = await _get_conversation(session, patient_id, doctor_id, lock=True)
    last_seq = conversation.last_seq if conversation is not None else 0
    own_read, peer_read = _read_cursors(conversation, actor)
    messages: list[Message] = []
    if conversation is not None:
        statement = select(Message).where(
            Message.conversation_id == conversation.id,
            Message.seq <= last_seq,
        )
        if before_seq is not None:
            statement = statement.where(Message.seq < before_seq).order_by(Message.seq.desc())
        elif after_seq is not None:
            statement = statement.where(Message.seq > after_seq).order_by(Message.seq.asc())
        else:
            statement = statement.order_by(Message.seq.desc())
        fetched = list((await session.scalars(statement.limit(limit + 1))).all())
        messages = fetched[:limit]
        if after_seq is None:
            messages.reverse()

    unread_count = await _unread_count(session, conversation, actor, own_read)
    if messages and conversation is not None:
        before_count = await session.scalar(
            select(func.count(Message.id)).where(
                Message.conversation_id == conversation.id,
                Message.seq < messages[0].seq,
            )
        )
        after_count = await session.scalar(
            select(func.count(Message.id)).where(
                Message.conversation_id == conversation.id,
                Message.seq > messages[-1].seq,
                Message.seq <= last_seq,
            )
        )
        next_before = messages[0].seq if before_count else None
        next_after = messages[-1].seq if after_count else None
    else:
        next_before = next_after = None

    statuses = await freetext.statuses(
        session, source_type="message", source_ids=[message.id for message in messages]
    )
    await identity.log_access(
        session, actor_id=actor.id, action="messaging.history", patient_id=patient_id
    )
    items = (
        [message_out(message, conversation, statuses) for message in messages]
        if conversation is not None
        else []
    )
    return MessageHistoryOut(
        items=items,
        doctor_id=doctor_id,
        last_seq=last_seq,
        read_through_seq=own_read,
        peer_read_through_seq=peer_read,
        unread_count=unread_count,
        next_before_seq=next_before,
        next_after_seq=next_after,
    )


async def mark_read(
    session: AsyncSession, actor: UserOut, patient_id: UUID, body: ReadCreateIn
) -> ReadOut:
    """Монотонно передвинуть курсор; повтор без роста не публикует событие."""
    doctor_id = await _lock_current_pair(
        session, actor, patient_id, expected_doctor_id=body.expected_doctor_id
    )
    conversation = await _get_conversation(session, patient_id, doctor_id, lock=True)
    last_seq = conversation.last_seq if conversation is not None else 0
    if body.through_seq < 0 or body.through_seq > last_seq:
        raise MessagingError(422, "invalid_cursor", "Read cursor is outside the conversation")

    own_read, _ = _read_cursors(conversation, actor)
    new_read = max(own_read, body.through_seq)
    if conversation is not None and new_read > own_read:
        if actor.role is Role.DOCTOR:
            conversation.doctor_read_through_seq = new_read
        else:
            conversation.patient_read_through_seq = new_read
        await realtime.publish(
            session,
            [patient_id, doctor_id],
            "message.read",
            {
                "patient_id": str(patient_id),
                "doctor_id": str(doctor_id),
                "reader_id": str(actor.id),
                "through_seq": new_read,
            },
        )
    unread_count = await _unread_count(session, conversation, actor, new_read)
    await identity.log_access(
        session, actor_id=actor.id, action="messaging.read", patient_id=patient_id
    )
    return ReadOut(read_through_seq=new_read, unread_count=unread_count)


async def list_unread(session: AsyncSession, actor: UserOut) -> list[UnreadOut]:
    """Сводка входящих непрочитанных сообщений только для текущих пар."""
    if actor.role is Role.DOCTOR:
        current_patients = await patients.list_patients(session, actor.id)
        patient_ids = sorted(patient.id for patient in current_patients)
        current_doctor_id = None
    elif actor.role is Role.PATIENT:
        current_doctor_id = await patients.lead_doctor_of_for_update(session, actor.id)
        patient_ids = [actor.id] if current_doctor_id is not None else []
    else:
        raise MessagingError(403, "forbidden", "This role cannot read conversations")

    result: list[UnreadOut] = []
    for patient_id in patient_ids:
        if actor.role is Role.DOCTOR:
            doctor_id = await patients.lead_doctor_of_for_update(session, patient_id)
            if doctor_id != actor.id:
                continue
        else:
            if current_doctor_id is None:
                continue
            doctor_id = current_doctor_id
        conversation = await _get_conversation(session, patient_id, doctor_id, lock=False)
        if conversation is None:
            continue
        own_read, _ = _read_cursors(conversation, actor)
        count = await _unread_count(session, conversation, actor, own_read)
        if count == 0:
            continue
        last_unread = await session.scalar(
            select(Message)
            .where(
                Message.conversation_id == conversation.id,
                Message.seq > own_read,
                Message.sender_id != actor.id,
            )
            .order_by(Message.seq.desc())
            .limit(1)
        )
        if last_unread is None:
            continue
        result.append(
            UnreadOut(
                patient_id=patient_id,
                doctor_id=doctor_id,
                unread_count=count,
                last_message_id=last_unread.id,
                last_message_at=last_unread.created_at,
            )
        )
    audit_patient_ids: tuple[UUID | None, ...] = tuple(patient_ids) if patient_ids else (None,)
    for audit_patient_id in audit_patient_ids:
        await identity.log_access(
            session,
            actor_id=actor.id,
            action="messaging.unread",
            patient_id=audit_patient_id,
        )
    return result


async def audit_denied(
    session: AsyncSession, *, actor_id: UUID, patient_id: UUID | None, action: str
) -> None:
    """Зафиксировать отказ после отката основной транзакции отдельным коммитом."""
    try:
        await session.rollback()
        await identity.log_access(
            session,
            actor_id=actor_id,
            action=f"{action}.denied",
            patient_id=patient_id,
        )
        await session.commit()
    except SQLAlchemyError as error:
        with contextlib.suppress(SQLAlchemyError):
            await session.rollback()
        log.error("messaging.audit_failed", error_type=type(error).__name__)


async def _lock_current_pair(
    session: AsyncSession,
    actor: UserOut,
    patient_id: UUID,
    *,
    expected_doctor_id: UUID | None,
) -> UUID:
    if actor.role is Role.ADMIN:
        raise MessagingError(403, "forbidden", "This role cannot read conversations")
    if actor.role is Role.PATIENT and actor.id != patient_id:
        raise MessagingError(404, "not_found", "Conversation not found")
    doctor_id = await patients.lead_doctor_of_for_update(session, patient_id)
    if doctor_id is None:
        raise MessagingError(404, "not_found", "Conversation not found")
    if actor.role is Role.DOCTOR and doctor_id != actor.id:
        raise MessagingError(404, "not_found", "Conversation not found")
    if expected_doctor_id is not None and expected_doctor_id != doctor_id:
        raise MessagingError(409, "conversation_changed", "Conversation changed")
    return doctor_id


async def _lock_idempotency_key(session: AsyncSession, sender_id: UUID, client_id: UUID) -> None:
    key = f"messaging:idempotency:{sender_id}:{client_id}"
    await session.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(key, 0))))


async def _find_by_client_id(
    session: AsyncSession, sender_id: UUID, client_id: UUID
) -> tuple[Message, Conversation] | None:
    row = (
        await session.execute(
            select(Message, Conversation)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(Message.sender_id == sender_id, Message.client_message_id == client_id)
        )
    ).one_or_none()
    return (row[0], row[1]) if row is not None else None


async def _get_conversation(
    session: AsyncSession, patient_id: UUID, doctor_id: UUID, *, lock: bool
) -> Conversation | None:
    statement = select(Conversation).where(
        Conversation.patient_id == patient_id, Conversation.doctor_id == doctor_id
    )
    if lock:
        statement = statement.with_for_update()
    return await session.scalar(statement)


def _read_cursors(conversation: Conversation | None, actor: UserOut) -> tuple[int, int]:
    if conversation is None:
        return 0, 0
    if actor.role is Role.DOCTOR:
        return conversation.doctor_read_through_seq, conversation.patient_read_through_seq
    return conversation.patient_read_through_seq, conversation.doctor_read_through_seq


async def _unread_count(
    session: AsyncSession, conversation: Conversation | None, actor: UserOut, cursor: int
) -> int:
    if conversation is None:
        return 0
    count = await session.scalar(
        select(func.count(Message.id)).where(
            Message.conversation_id == conversation.id,
            Message.sender_id != actor.id,
            Message.seq > cursor,
        )
    )
    return int(count or 0)


async def messaging_status(
    session: AsyncSession, actor: UserOut, patient_id: UUID
) -> MessagingStatusOut:
    """Read the current pair's peer state and write the required access audit."""
    doctor_id = await _lock_current_pair(session, actor, patient_id, expected_doctor_id=None)
    peer_id = patient_id if actor.role is Role.DOCTOR else doctor_id
    peer = await identity.get_user(session, peer_id)
    if peer is None:
        raise MessagingError(404, "not_found", "Conversation not found")
    result = await presence.get_status(
        session,
        patient_id=patient_id,
        doctor_id=doctor_id,
        peer_id=peer_id,
        peer_full_name=peer.full_name,
    )
    await identity.log_access(
        session, actor_id=actor.id, action="messaging.status", patient_id=patient_id
    )
    return result


async def update_typing(
    session: AsyncSession,
    actor: UserOut,
    *,
    connection_id: UUID,
    session_key: str,
    patient_id: UUID,
    expected_doctor_id: UUID,
    active: bool,
) -> None:
    """Validate the current pair before applying connection-scoped input state."""
    doctor_id = await _lock_current_pair(
        session, actor, patient_id, expected_doctor_id=expected_doctor_id
    )
    await presence.set_typing(
        session,
        user_id=actor.id,
        connection_id=connection_id,
        session_key=session_key,
        patient_id=patient_id,
        doctor_id=doctor_id,
        active=active,
    )


async def touch_presence(
    session: AsyncSession,
    actor: UserOut,
    *,
    connection_id: UUID,
    session_key: str,
    session_expires_at: datetime,
) -> None:
    """Open or renew the validated connection's presence lease."""
    await presence.touch(
        session,
        actor,
        connection_id=connection_id,
        session_key=session_key,
        session_expires_at=session_expires_at,
    )


async def disconnect_presence(
    session: AsyncSession, actor: UserOut, *, connection_id: UUID
) -> None:
    """Remove this connection's lease and typing state after Hub disconnection."""
    await presence.disconnect(session, actor, connection_id=connection_id)


async def clear_typing(
    session: AsyncSession, *, user_id: UUID, patient_id: UUID, doctor_id: UUID
) -> None:
    """Clear all of the sender's tabs after one new message is accepted."""
    await presence.clear_typing(
        session, user_id=user_id, patient_id=patient_id, doctor_id=doctor_id
    )


async def can_deliver_event(
    session: AsyncSession, *, recipient_id: UUID, patient_id: UUID, doctor_id: UUID
) -> bool:
    """Revalidate a recipient against the current pair immediately before delivery."""
    return await presence.can_deliver(
        session, recipient_id=recipient_id, patient_id=patient_id, doctor_id=doctor_id
    )
