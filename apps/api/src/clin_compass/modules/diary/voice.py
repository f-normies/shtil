"""Приём аудио и связь с идемпотентной командой дневника."""

import contextlib
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import anyio
import structlog
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.core.storage import S3Storage, StorageError
from clin_compass.modules.diary import operations, service
from clin_compass.modules.diary.errors import DiaryError
from clin_compass.modules.diary.models import Audio, Operation
from clin_compass.modules.diary.schemas import (
    DiaryAudioCreateIn,
    DiaryCommandReceipt,
    DiaryVoiceEntryCreateIn,
)
from clin_compass.modules.identity.schemas import UserOut

log = structlog.get_logger()


class VoiceCommitUnknownError(Exception):
    """The commit result could not be verified; the uploaded object must be preserved."""


async def accept_voice(
    session: AsyncSession,
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: S3Storage,
    actor: UserOut,
    body: DiaryVoiceEntryCreateIn,
    audio: DiaryAudioCreateIn,
    data: bytes,
    *,
    timezone: ZoneInfo,
) -> tuple[DiaryCommandReceipt, bool]:
    """Store audio outside the database transaction and atomically accept its diary command."""
    request_hash = operations.canonical_command_hash(
        "entry.voice.create", body, audio=audio, include_defaults=True
    )
    try:
        prior = await service.check_voice_replay(session, actor, body, audio)
    except BaseException:
        await _rollback(session)
        raise
    if prior is not None:
        await session.commit()
        return prior, False
    await session.rollback()

    object_key = f"diary/{uuid4()}"
    try:
        await anyio.to_thread.run_sync(storage.put, object_key, data, audio.mime_type)
    except BaseException:
        await _delete_attempt(storage, object_key)
        raise
    stored_audio = audio.model_copy(update={"storage_key": object_key})
    try:
        receipt, created = await service.create_voice_entry(
            session, actor, body, stored_audio, timezone=timezone
        )
    except BaseException:
        await _rollback(session)
        await _delete_attempt(storage, object_key)
        raise

    try:
        await session.commit()
    except SQLAlchemyError:
        await _rollback(session)
        resolved = await _resolve_commit(
            sessionmaker,
            storage,
            actor_id=actor.id,
            operation_id=body.operation_id,
            request_hash=request_hash,
            object_key=object_key,
        )
        if resolved is None:
            raise VoiceCommitUnknownError from None
        if isinstance(resolved, DiaryError):
            raise resolved from None
        return resolved

    if not created:
        await _delete_attempt(storage, object_key)
    return receipt, created


async def _resolve_commit(
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: S3Storage,
    *,
    actor_id: UUID,
    operation_id: UUID,
    request_hash: str,
    object_key: str,
) -> tuple[DiaryCommandReceipt, bool] | DiaryError | None:
    """Check a fresh connection before deciding whether an attempted object is orphaned."""
    conflict: DiaryError | None = None
    same_object = False
    receipt: DiaryCommandReceipt | None = None
    try:
        async with sessionmaker() as check_session:
            operation = await check_session.scalar(
                select(Operation).where(
                    Operation.patient_id == actor_id,
                    Operation.operation_id == operation_id,
                )
            )
            if operation is None:
                return None
            if operation.request_hash != request_hash:
                conflict = DiaryError(
                    409, "operation_conflict", "Operation key was used for other data"
                )
            else:
                receipt = DiaryCommandReceipt.model_validate(operation.receipt)
                audio = await check_session.scalar(
                    select(Audio).where(Audio.entry_id == receipt.entry_id)
                )
                if audio is None:
                    return None
                same_object = audio.storage_key == object_key
    except SQLAlchemyError:
        return None
    if conflict is not None:
        await _delete_attempt(storage, object_key)
        return conflict
    if not same_object:
        await _delete_attempt(storage, object_key)
    if receipt is None:
        return None
    return receipt, same_object


async def _rollback(session: AsyncSession) -> None:
    with contextlib.suppress(SQLAlchemyError):
        await session.rollback()


async def _delete_attempt(storage: S3Storage, key: str) -> None:
    """Delete only this request's object after a definite rollback or a proven replay."""
    try:
        with anyio.CancelScope(shield=True):
            await anyio.to_thread.run_sync(storage.delete, key)
    except StorageError as error:
        log.warning("diary.voice_orphan_cleanup_failed", error_type=type(error).__name__)
