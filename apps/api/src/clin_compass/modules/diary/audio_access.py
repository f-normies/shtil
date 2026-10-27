"""Проверка доступа и журналирование чтения закрытого аудио дневника."""

import contextlib
from dataclasses import dataclass
from typing import NoReturn
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from clin_compass.modules.diary import access
from clin_compass.modules.diary.errors import DiaryError
from clin_compass.modules.diary.models import Audio, Entry
from clin_compass.modules.identity import service as identity
from clin_compass.modules.identity.schemas import UserOut

log = structlog.get_logger()


@dataclass(frozen=True, slots=True)
class AudioReference:
    """Внутренние данные для чтения потока; storage key не покидает service layer."""

    storage_key: str
    byte_size: int
    mime_type: str


async def patient_audio(session: AsyncSession, actor: UserOut, entry_id: UUID) -> AudioReference:
    """Авторизовать пациента на каждое чтение его текущей видимой записи."""
    entry, _doctor_id = await access.authorize_patient_entry(
        session,
        actor,
        entry_id=entry_id,
        action="diary.audio.read",
        lock_patient=True,
    )
    if entry is None or entry.patient_id != actor.id or entry.hidden_at is not None:
        await _deny_not_found(session, actor, actor.id, entry_id)
    audio = await _audio(session, entry.id)
    if audio is None:
        await _deny_not_found(session, actor, actor.id, entry_id)
    await session.commit()
    return _reference(audio)


async def doctor_audio(
    session: AsyncSession,
    actor: UserOut,
    patient_id: UUID,
    entry_id: UUID,
) -> AudioReference:
    """Проверить текущего ведущего врача и доступность видимого аудио."""
    await access.authorize_doctor(
        session,
        doctor_id=actor.id,
        patient_id=patient_id,
        action="diary.audio.read",
        subject=str(entry_id),
        lock_patient=True,
    )
    entry = await session.scalar(
        select(Entry).where(Entry.id == entry_id, Entry.patient_id == patient_id).with_for_update()
    )
    if entry is None or entry.hidden_at is not None:
        await _deny_not_found(session, actor, patient_id, entry_id)
    audio = await _audio(session, entry.id)
    if audio is None:
        await _deny_not_found(session, actor, patient_id, entry_id)
    await session.commit()
    return _reference(audio)


async def _audio(session: AsyncSession, entry_id: UUID) -> Audio | None:
    return await session.scalar(select(Audio).where(Audio.entry_id == entry_id))


def _reference(audio: Audio) -> AudioReference:
    return AudioReference(
        storage_key=audio.storage_key,
        byte_size=audio.byte_size,
        mime_type=audio.mime_type,
    )


async def _deny_not_found(
    session: AsyncSession,
    actor: UserOut,
    patient_id: UUID,
    entry_id: UUID,
) -> NoReturn:
    """Replace any provisional success audit with a denial committed separately."""
    try:
        await session.rollback()
        await identity.log_access(
            session,
            actor_id=actor.id,
            action="diary.audio.read.denied",
            patient_id=patient_id,
            subject=str(entry_id),
        )
        await session.commit()
    except SQLAlchemyError as error:
        with contextlib.suppress(SQLAlchemyError):
            await session.rollback()
        log.error("diary.audio_audit_failed", error_type=type(error).__name__)
    raise DiaryError(404, "not_found", "Audio not found")
