"""Снимок уже принятых отметок приёма для неизменяемой версии дневника."""

from datetime import date
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from clin_compass.modules.diary.models import IntakeMark, RevisionIntakeMark


async def snapshot_daily_intake(
    session: AsyncSession,
    *,
    patient_id: UUID,
    scheduled_on: date,
    revision_id: UUID,
) -> None:
    """Привязать к дневному итогу последние уже принятые версии отметок дня."""
    marks = list(
        await session.scalars(
            select(IntakeMark)
            .where(
                IntakeMark.patient_id == patient_id,
                IntakeMark.scheduled_on == scheduled_on,
            )
            .order_by(
                IntakeMark.prescription_id,
                IntakeMark.dose_ordinal,
                IntakeMark.version.desc(),
            )
        )
    )
    latest_by_slot: dict[tuple[UUID, int], IntakeMark] = {}
    for mark in marks:
        latest_by_slot.setdefault((mark.prescription_id, mark.dose_ordinal), mark)
    session.add_all(
        [
            RevisionIntakeMark(revision_id=revision_id, intake_mark_id=mark.id)
            for mark in latest_by_slot.values()
        ]
    )
