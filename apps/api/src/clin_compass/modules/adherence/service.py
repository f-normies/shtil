"""Отметки доз пациентом и приём за 14 дней для врача (спека #34, разделы 4 и 5).

Назначения читаются через `therapy.service`. Право доступа проверяет вызывающий: врачу —
`owned_patient`, пациенту — его собственный id из сессии.
"""

from collections.abc import Sequence
from datetime import date, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from clin_compass.modules.adherence import doses
from clin_compass.modules.adherence.models import DoseMark
from clin_compass.modules.adherence.schemas import (
    AdherenceDayOut,
    AdherenceOut,
    CourseOut,
    DayDoseOut,
    DayDosesOut,
    DoseOut,
    MarkIn,
    PastMarkIn,
    PeriodOut,
)
from clin_compass.modules.therapy import service as therapy
from clin_compass.modules.therapy.schemas import PrescriptionOut

# Отметка «пришла позже» допустима, но не задним числом через месяц (спека #34, раздел 4).
LATE_DAYS = 7


class DoseNotFoundError(Exception):
    """Назначения нет среди назначений пациента."""


class InvalidDoseError(Exception):
    """Такой дозы нет: день вне приёма, номер приёма больше кратности, день вне окна отметок."""


def now() -> datetime:
    """Текущее время по Москве. Тесты API подменяют его, чтобы не зависеть от часа запуска."""
    return datetime.now(doses.MOSCOW)


def course_of(p: PrescriptionOut) -> doses.Course:
    """Назначение по часам: с `started_at` до начала дня `ends_on` или до отмены."""
    ends_at = datetime.combine(p.ends_on, datetime.min.time(), doses.MOSCOW) if p.ends_on else None
    ends = [at for at in (ends_at, p.stopped_at) if at is not None]
    end = min(ends) if ends else None
    return doses.Course(
        id=p.id,
        slot_times=doses.slot_times(p.times_per_day, p.schedule),
        start=p.started_at,
        end=end,
        stopped=p.stopped_at is not None and p.stopped_at == end,
    )


async def day_doses(
    session: AsyncSession, patient_id: UUID, day: date, at: datetime
) -> DayDosesOut:
    """Дозы дня пациента с отметками и подписями приёмов. `due` — время приёма пришло к `at`.

    В день смены схемы прежнее назначение отдаёт дозы до отмены, новое — после начала.
    """
    prescriptions = await therapy.list_prescriptions(session, patient_id, at.date())
    state = doses.current_marks(await _marks(session, patient_id, day, day))
    items = []
    for p in prescriptions:
        course = course_of(p)
        labels = doses.slot_labels(p.times_per_day, p.schedule)
        for slot, label in enumerate(labels):
            if not doses.has_dose(course, day, slot):
                continue
            m = state.get((p.id, day, slot))
            items.append(
                DoseOut(
                    prescription_id=p.id,
                    trade_name=p.trade_name,
                    dose_mg=p.dose_mg,
                    slot=slot,
                    slot_label=label,
                    status="taken" if m and m.status == "taken" else "missed" if m else None,
                    marked_at=m.marked_at if m else None,
                    due=doses.is_due(course, day, slot, at),
                )
            )
    return DayDosesOut(day=day, doses=items)


async def mark(
    session: AsyncSession, patient_id: UUID, marks: Sequence[MarkIn], today: date
) -> None:
    """Пишет отметки, которые меняют состояние дозы. Коммитит вызывающий.

    Ошибка в любой отметке — исключение до записи: не пишется ни одна.
    """
    prescriptions = await therapy.list_prescriptions(session, patient_id, today)
    courses = {p.id: course_of(p) for p in prescriptions}
    for m in marks:
        course = courses.get(m.prescription_id)
        if course is None:
            raise DoseNotFoundError(m.prescription_id)
        if not today - timedelta(days=LATE_DAYS) <= m.day <= today:
            raise InvalidDoseError(f"day {m.day} is outside the marking window")
        if not doses.has_dose(course, m.day, m.slot):
            raise InvalidDoseError(f"no dose on {m.day}, slot {m.slot}")
    days = [m.day for m in marks]
    state = doses.current_marks(await _marks(session, patient_id, min(days), max(days)))
    for m in marks:
        now = state.get((m.prescription_id, m.day, m.slot))
        if (now.status if now else None) == m.status:
            continue
        session.add(
            DoseMark(
                patient_id=patient_id,
                prescription_id=m.prescription_id,
                day=m.day,
                slot=m.slot,
                status=m.status or "cleared",
            )
        )
    await session.flush()


async def record_diary_intake(
    session: AsyncSession,
    *,
    patient_id: UUID,
    prescription_id: UUID,
    day: date,
    slot: int,
    taken: bool,
) -> None:
    """Project an explicit #31 intake report into the #34 history in the same transaction.

    The diary caller validates the prescription and its historical scheme before this call.
    Unmarked means clear; it never creates an automatic missed-dose mark.
    """
    status: doses.MarkStatus = "taken" if taken else "cleared"
    key = (prescription_id, day, slot)
    current = doses.current_marks(await _marks(session, patient_id, day, day)).get(key)
    if (current.status if current else None) == ("taken" if taken else None):
        return
    session.add(
        DoseMark(
            patient_id=patient_id,
            prescription_id=prescription_id,
            day=day,
            slot=slot,
            status=status,
        )
    )
    await session.flush()


async def add_past_marks(
    session: AsyncSession, patient_id: UUID, prescription_id: UUID, marks: Sequence[PastMarkIn]
) -> bool:
    """Отметки сида с их временем, без окна отметок. Есть отметки у пациента — ничего не пишет."""
    if await session.scalar(select(DoseMark.id).where(DoseMark.patient_id == patient_id).limit(1)):
        return False
    session.add_all(
        DoseMark(
            patient_id=patient_id,
            prescription_id=prescription_id,
            day=m.day,
            slot=m.slot,
            status=m.status,
            marked_at=m.marked_at,
        )
        for m in marks
    )
    await session.flush()
    return True


async def summary(session: AsyncSession, patient_id: UUID, at: datetime) -> AdherenceOut:
    """Приём за 14 дней к моменту `at`: при действующем назначении — до сегодня, иначе —
    конец курса. Пустые дозы, время которых не пришло, не считаются."""
    prescriptions = await therapy.list_prescriptions(session, patient_id, at.date())
    courses = [course_of(p) for p in prescriptions]
    period = doses.period_of(courses, at)
    if period.course == "none":
        return AdherenceOut(
            period=None,
            course="none",
            prescriptions=[],
            days=[],
            planned=0,
            taken=0,
            missed=0,
            unmarked=0,
            marked_any=False,
        )
    result = doses.summarize(
        courses, await _marks(session, patient_id, period.start, period.end), period, at
    )
    shown = {d.prescription_id for day in result.days for d in day.doses}
    return AdherenceOut(
        period=PeriodOut(start=period.start, end=period.end),
        course=period.course,
        prescriptions=[_course_out(p) for p in prescriptions if p.id in shown],
        days=[
            AdherenceDayOut(
                day=d.day,
                doses=[
                    DayDoseOut(
                        prescription_id=x.prescription_id,
                        slot=x.slot,
                        status=x.status,
                        marked_at=x.marked_at,
                    )
                    for x in d.doses
                ],
            )
            for d in result.days
        ],
        planned=result.planned,
        taken=result.taken,
        missed=result.missed,
        unmarked=result.unmarked,
        marked_any=result.marked_any,
    )


def _course_out(p: PrescriptionOut) -> CourseOut:
    return CourseOut(
        id=p.id,
        trade_name=p.trade_name,
        dose_mg=p.dose_mg,
        times_per_day=p.times_per_day,
        schedule=p.schedule,
        slot_labels=doses.slot_labels(p.times_per_day, p.schedule),
        started_on=p.started_at.astimezone(doses.MOSCOW).date(),
        ends_on=p.ends_on,
        stopped_on=p.stopped_at.astimezone(doses.MOSCOW).date() if p.stopped_at else None,
    )


async def _marks(
    session: AsyncSession, patient_id: UUID, start: date, end: date
) -> list[doses.Mark]:
    rows = await session.scalars(
        select(DoseMark)
        .where(DoseMark.patient_id == patient_id, DoseMark.day.between(start, end))
        .order_by(DoseMark.marked_at, DoseMark.id)
    )
    return [
        doses.Mark(
            prescription_id=r.prescription_id,
            day=r.day,
            slot=r.slot,
            status=r.status,
            marked_at=r.marked_at,
        )
        for r in rows
    ]
