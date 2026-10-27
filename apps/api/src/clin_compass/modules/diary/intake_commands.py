"""Команды и публичное чтение явных отметок пациента о приёме."""

from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from clin_compass.core import realtime
from clin_compass.modules.adherence import service as adherence
from clin_compass.modules.diary import access
from clin_compass.modules.diary.errors import DiaryError
from clin_compass.modules.diary.models import IntakeMark
from clin_compass.modules.diary.operations import (
    canonical_command_hash,
    replay_receipt,
    save_receipt,
)
from clin_compass.modules.diary.schemas import IntakeMarkCreateIn, IntakeMarkOut, IntakeMarksOut
from clin_compass.modules.identity.schemas import UserOut
from clin_compass.modules.therapy import service as therapy


async def create_intake_mark(
    session: AsyncSession,
    actor: UserOut,
    body: IntakeMarkCreateIn,
) -> tuple[IntakeMarkOut, bool]:
    """Сохранить новую версию отметки после проверки назначения на её календарную дату."""
    doctor_id = await access.authorize_self_patient(
        session, actor, action="diary.intake.mark", lock_patient=True
    )
    request_hash = canonical_command_hash("intake.mark", body)
    prior = await replay_receipt(
        session,
        patient_id=actor.id,
        operation_id=body.operation_id,
        operation_type="intake.mark",
        request_hash=request_hash,
    )
    if prior is not None:
        return IntakeMarkOut.model_validate(prior), False
    scheme = await therapy.prescription_snapshot_for_date(
        session,
        patient_id=actor.id,
        prescription_id=body.prescription_id,
        scheduled_on=body.scheduled_on,
    )
    if scheme is None or body.dose_ordinal > scheme.times_per_day:
        raise DiaryError(409, "intake_scheme_conflict", "Prescription is not active for this dose")
    previous = await session.scalar(
        select(IntakeMark)
        .where(
            IntakeMark.patient_id == actor.id,
            IntakeMark.prescription_id == body.prescription_id,
            IntakeMark.scheduled_on == body.scheduled_on,
            IntakeMark.dose_ordinal == body.dose_ordinal,
        )
        .order_by(IntakeMark.version.desc())
        .limit(1)
    )
    mark = IntakeMark(
        patient_id=actor.id,
        prescription_id=body.prescription_id,
        scheduled_on=body.scheduled_on,
        dose_ordinal=body.dose_ordinal,
        status=body.status,
        occurred_at=body.occurred_at,
        version=previous.version + 1 if previous else 1,
        scheme_snapshot=scheme.model_dump(mode="json"),
    )
    session.add(mark)
    await session.flush()
    await adherence.record_diary_intake(
        session,
        patient_id=actor.id,
        prescription_id=body.prescription_id,
        day=body.scheduled_on,
        slot=body.dose_ordinal - 1,
        taken=body.status == "taken",
    )
    result = _mark_out(mark)
    save_receipt(
        session,
        patient_id=actor.id,
        operation_id=body.operation_id,
        operation_type="intake.mark",
        request_hash=request_hash,
        receipt=result,
    )
    await realtime.publish(
        session,
        [actor.id, doctor_id],
        "diary.changed",
        {"patient_id": str(actor.id), "entry_id": str(mark.id)},
    )
    await session.flush()
    return result, True


async def list_patient_intake_marks(
    session: AsyncSession,
    actor: UserOut,
    *,
    scheduled_on: date,
) -> IntakeMarksOut:
    """Прочитать последние явные отметки пациента за дату с успешным аудитом."""
    await access.authorize_self_patient(session, actor, action="diary.intake.list")
    rows = await session.scalars(
        select(IntakeMark)
        .where(IntakeMark.patient_id == actor.id, IntakeMark.scheduled_on == scheduled_on)
        .order_by(IntakeMark.prescription_id, IntakeMark.dose_ordinal, IntakeMark.version.desc())
    )
    return IntakeMarksOut(items=_latest_slots(rows))


async def list_intake_marks(
    session: AsyncSession,
    *,
    doctor_id: UUID,
    patient_id: UUID,
    start: date,
    end: date,
) -> IntakeMarksOut:
    """Публичный контракт для #34: последние версии отметок врача за период включительно."""
    await access.authorize_doctor(
        session,
        doctor_id=doctor_id,
        patient_id=patient_id,
        action="diary.intake.patient_list",
    )
    if end < start or (end - start).days > 89:
        raise DiaryError(422, "invalid_period", "Intake period must be between 1 and 90 days")
    rows = await session.scalars(
        select(IntakeMark)
        .where(
            IntakeMark.patient_id == patient_id,
            IntakeMark.scheduled_on >= start,
            IntakeMark.scheduled_on <= end,
        )
        .order_by(
            IntakeMark.scheduled_on,
            IntakeMark.prescription_id,
            IntakeMark.dose_ordinal,
            IntakeMark.version.desc(),
        )
    )
    return IntakeMarksOut(items=_latest_slots(rows))


def _latest_slots(rows: Any) -> list[IntakeMarkOut]:
    latest: dict[tuple[UUID, date, int], IntakeMarkOut] = {}
    for row in rows:
        key = (row.prescription_id, row.scheduled_on, row.dose_ordinal)
        latest.setdefault(key, _mark_out(row))
    return list(latest.values())


def _mark_out(row: IntakeMark) -> IntakeMarkOut:
    return IntakeMarkOut(
        id=row.id,
        prescription_id=row.prescription_id,
        scheduled_on=row.scheduled_on,
        dose_ordinal=row.dose_ordinal,
        status=row.status,
        occurred_at=row.occurred_at,
        source=row.source,
        version=row.version,
        scheme_snapshot=row.scheme_snapshot,
        received_at=row.received_at,
    )
