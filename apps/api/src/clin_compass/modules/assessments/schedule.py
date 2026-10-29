"""Расписание опросов пациента: плановые даты шкалы и пропуски (спека #111).

Расписание не хранится: оно вычисляется из дня регистрации и ответов по данным
`knowledge/scale-schedule.yaml` и выбору врача из `settings.py` (спека #180).
Отдельно от `service.py`: тот уже около 400 строк.
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

from clin_compass.modules.assessments import settings
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
    since: date | None = None,
) -> list[Planned]:
    """Плановые даты от регистрации до ближайшей будущей, правило спеки #111.

    `answers` — в порядке `completed_at`. Ответ относится к последней плановой дате
    не позже него, даже после срока пропуска. Ответ раньше плановой даты только
    сдвигает цепочку. Следующая дата — через `period_days` после ответа.

    `since` — день, когда врач изменил настройки шкалы (спека #180). Плановая дата
    раньше него не становится пропуском, а цепочка не начинается раньше него.
    """
    period = timedelta(days=period_days)
    result: list[Planned] = []
    current = registered_on
    for answer in answers:
        if since is not None and answer.on >= since:
            current = max(current, since)
        if answer.on >= current:
            skipped = (answer.on - current).days // period_days
            result += [Planned(current + k * period, "missed") for k in range(skipped)]
            result.append(Planned(current + skipped * period, "done", answer.id))
        current = answer.on + period
    if since is not None:
        current = max(current, since)
    while current <= today:
        late = (today - current).days > grace_days
        result.append(Planned(current, "missed" if late else "due"))
        current += period
    result.append(Planned(current, "upcoming"))
    if since is None:
        return result
    return [item for item in result if item.status != "missed" or item.planned_on >= since]


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


@dataclass(frozen=True)
class ScalePlan:
    """Плановые даты шкалы пациента с действующим у него периодом."""

    entry: ScheduledScale
    period_days: int
    planned: list[Planned]


async def _plans(
    session: AsyncSession,
    catalog: ScaleCatalog,
    patient_id: UUID,
    registered: datetime,
    zone: ZoneInfo,
    today: date,
) -> list[ScalePlan]:
    """Плановые даты включённых шкал с периодом пациента (спеки #111 и #180)."""
    chosen = await settings.rows_for(session, patient_id)
    entries = [
        entry
        for entry in settings.scheduled(catalog)
        if entry.scale not in chosen or chosen[entry.scale].enabled
    ]
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
    result: list[ScalePlan] = []
    for entry in entries:
        row = chosen.get(entry.scale)
        period = settings.period_of(catalog, entry, row)
        planned = plan(
            local_day(registered, zone),
            answers.get(entry.scale, []),
            period_days=period,
            grace_days=grace,
            today=today,
            since=local_day(row.updated_at, zone) if row else None,
        )
        result.append(ScalePlan(entry, period, planned))
    return result


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
                scale_id=item.entry.scale,
                scale_title=catalog.scales[item.entry.scale].title,
                due_on=due_on(item.planned),
            )
            for item in plans
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
                scale_id=item.entry.scale,
                period_days=item.period_days,
                grace_days=grace,
                planned=[PlannedDateOut.model_validate(planned) for planned in item.planned],
            )
            for item in plans
            if scale_id is None or item.entry.scale == scale_id
        ],
    )
