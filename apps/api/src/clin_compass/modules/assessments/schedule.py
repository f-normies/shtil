"""Расписание опросов пациента: плановые даты шкалы и пропуски (спека #111).

Расписание не хранится: оно вычисляется из дня регистрации и ответов по данным
`knowledge/scale-schedule.yaml`. Отдельно от `service.py`: тот уже около 400 строк.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from clin_compass_knowledge.schemas.scale_schedule import ScheduledScale
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from clin_compass.modules.assessments.models import AssessmentResult
from clin_compass.modules.assessments.schemas import (
    PatientScheduleOut,
    PlannedDateOut,
    ScaleDueOut,
    ScalePlanOut,
    ScheduleOut,
)
from clin_compass.modules.assessments.service import ScaleCatalog
from clin_compass.modules.identity import service as identity
from clin_compass.modules.identity.schemas import UserOut
from clin_compass.modules.patients import service as patients
from clin_compass.modules.patients.schemas import PatientSummary

PlannedStatus = Literal["done", "missed", "due", "upcoming"]


@dataclass(frozen=True)
class Answer:
    """Ответ по шкале: календарный день заполнения и id результата."""

    on: date
    id: UUID


@dataclass(frozen=True)
class Planned:
    """Плановая дата и её статус; `assessment_id` — ответ, отнесённый к дате."""

    planned_on: date
    status: PlannedStatus
    assessment_id: UUID | None = None


def plan(
    registered_on: date,
    answers: Sequence[Answer],
    *,
    period_days: int,
    grace_days: int,
    today: date,
) -> list[Planned]:
    """Плановые даты от регистрации до ближайшей будущей, правило спеки #111.

    `answers` — в порядке `completed_at`. Ответ относится к последней плановой дате
    не позже него, даже после срока пропуска. Ответ раньше плановой даты только
    сдвигает цепочку. Следующая дата — через `period_days` после ответа.
    """
    period = timedelta(days=period_days)
    result: list[Planned] = []
    current = registered_on
    for answer in answers:
        if answer.on >= current:
            skipped = (answer.on - current).days // period_days
            result += [Planned(current + k * period, "missed") for k in range(skipped)]
            result.append(Planned(current + skipped * period, "done", answer.id))
        current = answer.on + period
    while current <= today:
        late = (today - current).days > grace_days
        result.append(Planned(current, "missed" if late else "due"))
        current += period
    result.append(Planned(current, "upcoming"))
    return result


def due_on(planned: Sequence[Planned]) -> date:
    """День опроса для пациента: открытая дата без ответа, иначе ближайшая будущая.

    Пропущенный опрос предлагается и дальше: поздний ответ закрывает пропуск.
    """
    if len(planned) > 1 and planned[-2].status in ("due", "missed"):
        return planned[-2].planned_on
    return planned[-1].planned_on


def local_day(moment: datetime, zone: ZoneInfo) -> date:
    """Календарный день момента в поясе расписания."""
    return moment.astimezone(zone).date()


def _scheduled(catalog: ScaleCatalog) -> list[ScheduledScale]:
    """Шкалы расписания, которые выдаются пациенту. Черновик расписания не действует."""
    schedule = catalog.schedule
    if schedule is None or schedule.status != "reviewed":
        return []
    return [
        entry
        for entry in schedule.scales
        if entry.scale in catalog.scales and catalog.is_available(catalog.scales[entry.scale])
    ]


async def _plans(
    session: AsyncSession,
    catalog: ScaleCatalog,
    patient_id: UUID,
    registered: datetime,
    zone: ZoneInfo,
    today: date,
) -> list[tuple[ScheduledScale, list[Planned]]]:
    entries = _scheduled(catalog)
    if not entries or catalog.schedule is None:
        return []
    rows = await session.execute(
        select(AssessmentResult.scale_id, AssessmentResult.completed_at, AssessmentResult.id)
        .where(
            AssessmentResult.patient_id == patient_id,
            AssessmentResult.scale_id.in_([entry.scale for entry in entries]),
        )
        .order_by(AssessmentResult.completed_at, AssessmentResult.id)
    )
    answers: dict[str, list[Answer]] = {}
    for scale_id, completed_at, result_id in rows:
        answers.setdefault(scale_id, []).append(Answer(local_day(completed_at, zone), result_id))
    grace = catalog.schedule.grace_days
    return [
        (
            entry,
            plan(
                local_day(registered, zone),
                answers.get(entry.scale, []),
                period_days=entry.period_days,
                grace_days=grace,
                today=today,
            ),
        )
        for entry in entries
    ]


async def own_schedule(
    session: AsyncSession, catalog: ScaleCatalog, patient: UserOut, zone: ZoneInfo
) -> ScheduleOut:
    """Дни опроса вошедшего пациента. Чтение пишется в журнал доступа."""
    await identity.log_access(
        session, actor_id=patient.id, action="assessments.schedule", patient_id=patient.id
    )
    today = datetime.now(zone).date()
    registered = await patients.registered_at(session, patient.id)
    if registered is None:
        return ScheduleOut(today=today, items=[])
    plans = await _plans(session, catalog, patient.id, registered, zone, today)
    return ScheduleOut(
        today=today,
        items=[
            ScaleDueOut(
                scale_id=entry.scale,
                scale_title=catalog.scales[entry.scale].title,
                due_on=due_on(planned),
            )
            for entry, planned in plans
        ],
    )


async def patient_schedule(
    session: AsyncSession,
    catalog: ScaleCatalog,
    patient: PatientSummary,
    zone: ZoneInfo,
    scale_id: str | None = None,
) -> PatientScheduleOut:
    """Плановые даты шкал пациента для ведущего врача.

    `PatientSummary` выдаёт только проверка владения `patients.deps.owned_patient`. Она же
    пишет обращение и отказ в журнал доступа.
    """
    today = datetime.now(zone).date()
    plans = await _plans(session, catalog, patient.id, patient.created_at, zone, today)
    grace = catalog.schedule.grace_days if catalog.schedule is not None else 0
    return PatientScheduleOut(
        today=today,
        items=[
            ScalePlanOut(
                scale_id=entry.scale,
                period_days=entry.period_days,
                grace_days=grace,
                planned=[PlannedDateOut.model_validate(item) for item in planned],
            )
            for entry, planned in plans
            if scale_id is None or entry.scale == scale_id
        ],
    )
