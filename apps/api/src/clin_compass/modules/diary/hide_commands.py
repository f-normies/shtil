"""Команда сокрытия записи без удаления её версий и источников."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from clin_compass.core import realtime
from clin_compass.modules.diary import access
from clin_compass.modules.diary.errors import DiaryError
from clin_compass.modules.diary.operations import (
    canonical_command_hash,
    replay_receipt,
    save_receipt,
)
from clin_compass.modules.diary.schemas import DiaryCommandReceipt, DiaryHideIn
from clin_compass.modules.identity.schemas import UserOut


async def hide_entry(
    session: AsyncSession,
    actor: UserOut,
    entry_id: UUID,
    body: DiaryHideIn,
) -> tuple[DiaryCommandReceipt, bool]:
    """Скрыть запись, оставив ревизии, источники и отметки приёма неизменными."""
    entry, doctor_id = await access.authorize_patient_entry(
        session,
        actor,
        entry_id=entry_id,
        action="diary.entry.hide",
        lock_patient=True,
    )
    request_hash = canonical_command_hash("entry.hide", body)
    prior = await replay_receipt(
        session,
        patient_id=actor.id,
        operation_id=body.operation_id,
        operation_type="entry.hide",
        request_hash=request_hash,
    )
    if prior is not None:
        return DiaryCommandReceipt.model_validate(prior), False
    if entry is None or entry.patient_id != actor.id or entry.hidden_at is not None:
        raise DiaryError(404, "not_found", "Diary entry not found")
    if body.expected_revision != entry.current_revision:
        raise DiaryError(
            409, "revision_conflict", "Entry has changed", current_version=entry.current_revision
        )
    entry.hidden_at = datetime.now(UTC)
    receipt = DiaryCommandReceipt(
        operation_id=body.operation_id,
        entry_id=entry.id,
        revision=entry.current_revision,
        hidden=True,
        received_at=entry.hidden_at,
    )
    save_receipt(
        session,
        patient_id=actor.id,
        operation_id=body.operation_id,
        operation_type="entry.hide",
        request_hash=request_hash,
        receipt=receipt,
    )
    await realtime.publish(
        session,
        [actor.id, doctor_id],
        "diary.changed",
        {"patient_id": str(actor.id), "entry_id": str(entry.id)},
    )
    await session.flush()
    return receipt, True
