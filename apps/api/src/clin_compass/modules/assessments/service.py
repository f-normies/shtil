"""Публичная логика каталога, записи и чтения результатов шкал."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from clin_compass.modules.alerts import service as alerts_service
from clin_compass.modules.alerts.deps import RuleBook
from clin_compass.modules.assessments import recording
from clin_compass.modules.assessments.catalog import (
    AssessmentError,
    Respondent,
    ScaleCatalog,
    assert_consistent,
    definition_snapshot,
)
from clin_compass.modules.assessments.models import AssessmentResult
from clin_compass.modules.assessments.schemas import (
    AssessmentCreatedOut,
    AssessmentCreateIn,
    AssessmentHistoryOut,
    AssessmentOut,
    ClinicianAssessmentIn,
    HistoryQuery,
    ScaleCatalogOut,
    ScaleDefinitionOut,
)
from clin_compass.modules.identity import service as identity
from clin_compass.modules.identity.schemas import Role, UserOut
from clin_compass.modules.patients.schemas import PatientSummary

# Другие модули и тесты берут каталог и ошибку отсюда; определены в catalog.py.
__all__ = [
    "AssessmentError",
    "ScaleCatalog",
    "create_for_patient",
    "create_result",
    "definition_snapshot",
    "get_definition",
    "list_all_for_owned_patient",
    "list_clinician_definitions",
    "list_definitions",
    "list_for_owned_patient",
    "list_own",
]


async def list_definitions(session: AsyncSession, catalog: ScaleCatalog) -> ScaleCatalogOut:
    """Доступные пациенту шкалы после сверки с уже принятыми снимками определений."""
    available = catalog.list_available()
    await assert_consistent(session, [definition.scale for definition in available.items])
    return available


async def list_clinician_definitions(
    session: AsyncSession, catalog: ScaleCatalog
) -> ScaleCatalogOut:
    """Шкалы, которые заполняет врач (#94), после той же сверки снимков."""
    available = catalog.list_available("clinician")
    await assert_consistent(session, [definition.scale for definition in available.items])
    return available


async def get_definition(
    session: AsyncSession, catalog: ScaleCatalog, scale_id: str, reader: UserOut
) -> ScaleDefinitionOut:
    """Шкала для читателя: пациенту — только его шкалы, врачу — любая доступная.

    Недоступная — 404, изменённая без новой версии — 503.
    """
    respondent: Respondent | None = None if reader.role is Role.DOCTOR else "patient"
    definition = catalog.get_available(scale_id, respondent)
    if definition is None:
        raise AssessmentError(404, "scale_unavailable", "Scale is unavailable")
    await assert_consistent(session, [definition.scale])
    return definition


async def create_result(
    session: AsyncSession,
    catalog: ScaleCatalog,
    rules: RuleBook,
    *,
    patient_id: UUID,
    body: AssessmentCreateIn,
) -> tuple[AssessmentCreatedOut, bool]:
    """Самоотчёт пациента. Возвращает результат и признак новой записи; коммитит вызывающий.

    `alert_raised` считается и при повторе: срабатывание сигнала хранит id результата.
    """
    if body.patient_id != patient_id:
        raise AssessmentError(403, "patient_mismatch", "Patient identifier does not match session")
    submission = recording.Submission(
        patient_id=patient_id,
        submission_id=body.submission_id,
        scale_id=body.scale_id,
        scale_version=body.scale_version,
        completed_at=body.completed_at,
        answers=body.answers,
        respondent="patient",
    )
    result, created = await recording.record(session, catalog, rules, submission)
    raised = await alerts_service.alert_raised_by(session, str(result.id))
    return AssessmentCreatedOut.of(result, alert_raised=raised), created


async def create_for_patient(
    session: AsyncSession,
    catalog: ScaleCatalog,
    rules: RuleBook,
    *,
    patient: PatientSummary,
    doctor: UserOut,
    body: ClinicianAssessmentIn,
) -> tuple[AssessmentOut, bool]:
    """Результат шкалы врача (#94). Время — момент отправки; коммитит вызывающий.

    `PatientSummary` выдаёт только проверка владения `patients.deps.owned_patient`.
    """
    submission = recording.Submission(
        patient_id=patient.id,
        submission_id=body.submission_id,
        scale_id=body.scale_id,
        scale_version=body.scale_version,
        completed_at=datetime.now(UTC),
        answers=body.answers,
        respondent="clinician",
        recorded_by=doctor.id,
    )
    return await recording.record(session, catalog, rules, submission)


async def list_own(
    session: AsyncSession, patient: UserOut, query: HistoryQuery
) -> AssessmentHistoryOut:
    """История вошедшего пациента. Каждое чтение пишется в журнал доступа."""
    await identity.log_access(
        session, actor_id=patient.id, action="assessments.list", patient_id=patient.id
    )
    return await _history(session, patient.id, query, own=True)


async def list_for_owned_patient(
    session: AsyncSession, patient: PatientSummary, query: HistoryQuery
) -> AssessmentHistoryOut:
    """История пациента для его ведущего врача.

    `PatientSummary` выдаёт только проверка владения `patients.deps.owned_patient`. Она же
    пишет обращение и отказ в журнал доступа.
    """
    return await _history(session, patient.id, query)


async def list_all_for_owned_patient(
    session: AsyncSession, patient: PatientSummary
) -> list[AssessmentOut]:
    """Все результаты пациента по времени заполнения: для сводки между визитами (#38).

    `PatientSummary` выдаёт только проверка владения `patients.deps.owned_patient`.
    """
    rows = await session.scalars(
        select(AssessmentResult)
        .where(AssessmentResult.patient_id == patient.id)
        .order_by(AssessmentResult.completed_at, AssessmentResult.id)
    )
    return [AssessmentOut.model_validate(row) for row in rows]


async def _history(
    session: AsyncSession, patient_id: UUID, query: HistoryQuery, *, own: bool = False
) -> AssessmentHistoryOut:
    """Страница результатов в порядке поступления на сервер, новые первыми.

    `own` — история для самого пациента: без результатов, внесённых врачом (#94).
    """
    statement = select(AssessmentResult).where(AssessmentResult.patient_id == patient_id)
    if own:
        statement = statement.where(AssessmentResult.recorded_by.is_(None))
    if query.scale_id is not None:
        statement = statement.where(AssessmentResult.scale_id == query.scale_id)
    if query.before is not None:
        statement = statement.where(AssessmentResult.id < query.before)
    statement = statement.order_by(AssessmentResult.id.desc()).limit(query.limit + 1)
    rows = list((await session.scalars(statement)).all())
    has_more = len(rows) > query.limit
    page = rows[: query.limit]
    return AssessmentHistoryOut(
        items=[AssessmentOut.model_validate(row) for row in page],
        next_cursor=page[-1].id if has_more and page else None,
    )
