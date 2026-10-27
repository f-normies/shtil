"""HTTP-роутеры каталога и результатов заполнения шкал."""

from typing import Annotated, Any, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse

from clin_compass.core.deps import AppSettings, DbSession
from clin_compass.core.errors import PROBLEM_JSON, ProblemOut, ValidationProblemOut, problem
from clin_compass.modules.alerts.deps import AlertRules
from clin_compass.modules.assessments import schedule, service
from clin_compass.modules.assessments.schemas import (
    AssessmentCreatedOut,
    AssessmentCreateIn,
    AssessmentHistoryOut,
    AssessmentOut,
    ClinicianAssessmentIn,
    HistoryQuery,
    PatientScheduleOut,
    ScaleCatalogOut,
    ScaleDefinitionOut,
    ScheduleOut,
)
from clin_compass.modules.assessments.service import ScaleCatalog
from clin_compass.modules.identity.deps import (
    CurrentUser,
    DoctorUser,
    PatientUser,
    require_role,
)
from clin_compass.modules.identity.schemas import Role, UserOut
from clin_compass.modules.patients.deps import owned_patient
from clin_compass.modules.patients.schemas import PatientSummary

router = APIRouter(prefix="/assessments", tags=["assessments"])
patient_history_router = APIRouter(tags=["assessments"])

_PROBLEM_CONTENT: dict[str, Any] = {
    PROBLEM_JSON: {"schema": ProblemOut.model_json_schema()},
}
_VALIDATION_PROBLEM_CONTENT: dict[str, Any] = {
    PROBLEM_JSON: {
        "schema": {
            "anyOf": [
                ProblemOut.model_json_schema(),
                ValidationProblemOut.model_json_schema(),
            ]
        }
    }
}
_AUTH_PROBLEM_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"description": "Authentication is required", "content": _PROBLEM_CONTENT},
    403: {"description": "The authenticated role is not allowed", "content": _PROBLEM_CONTENT},
}
_DEFINITION_CONFLICT_RESPONSE: dict[str, Any] = {
    "description": "Scale definition conflicts with an accepted snapshot",
    "content": _PROBLEM_CONTENT,
}


def _catalog(request: Request) -> ScaleCatalog:
    """Каталог создаётся один раз при старте из проверенной базы знаний."""
    return cast(ScaleCatalog, request.app.state.assessment_catalog)


def _history_query(
    scale_id: Annotated[str | None, Query(min_length=1)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    before: UUID | None = None,
) -> HistoryQuery:
    """Фильтр по шкале и курсор страницы, общие для обеих историй."""
    return HistoryQuery(scale_id=scale_id, limit=limit, before=before)


Catalog = Annotated[ScaleCatalog, Depends(_catalog)]
History = Annotated[HistoryQuery, Depends(_history_query)]
# Определение шкалы не содержит данных пациента: его читают пациент и врач.
ScaleReader = Annotated[UserOut, Depends(require_role(Role.PATIENT, Role.DOCTOR))]


def _problem(error: service.AssessmentError) -> JSONResponse:
    return problem(error.status, error.detail, code=error.code, **error.fields)


@router.get(
    "/scales",
    response_model=ScaleCatalogOut,
    responses={**_AUTH_PROBLEM_RESPONSES, 503: _DEFINITION_CONFLICT_RESPONSE},
)
async def list_scales(
    _patient: PatientUser, session: DbSession, catalog: Catalog
) -> ScaleCatalogOut | JSONResponse:
    """Список определений, доступных пациенту."""
    try:
        return await service.list_definitions(session, catalog)
    except service.AssessmentError as error:
        return _problem(error)


@router.get(
    "/clinician-scales",
    response_model=ScaleCatalogOut,
    responses={**_AUTH_PROBLEM_RESPONSES, 503: _DEFINITION_CONFLICT_RESPONSE},
)
async def list_clinician_scales(
    _doctor: DoctorUser, session: DbSession, catalog: Catalog
) -> ScaleCatalogOut | JSONResponse:
    """Шкалы, которые заполняет врач после клинического интервью (#94)."""
    try:
        return await service.list_clinician_definitions(session, catalog)
    except service.AssessmentError as error:
        return _problem(error)


@router.get("/schedule", response_model=ScheduleOut, responses=_AUTH_PROBLEM_RESPONSES)
async def get_own_schedule(
    patient: PatientUser, session: DbSession, catalog: Catalog, settings: AppSettings
) -> ScheduleOut:
    """День опроса по каждой шкале расписания (спека #111); чтение пишется в журнал."""
    result = await schedule.own_schedule(session, catalog, patient, settings.timezone)
    await session.commit()
    return result


@router.get(
    "/scales/{scale_id}",
    response_model=ScaleDefinitionOut,
    responses={
        **_AUTH_PROBLEM_RESPONSES,
        404: {"description": "Scale is unavailable", "content": _PROBLEM_CONTENT},
        503: _DEFINITION_CONFLICT_RESPONSE,
    },
)
async def get_scale(
    scale_id: str, _reader: ScaleReader, session: DbSession, catalog: Catalog
) -> ScaleDefinitionOut | JSONResponse:
    """Определение шкалы. Пациенту — только его шкалы, врачу — и шкалы врача (#13, #94)."""
    try:
        return await service.get_definition(session, catalog, scale_id, _reader)
    except service.AssessmentError as error:
        return _problem(error)


@router.post(
    "",
    response_model=AssessmentCreatedOut,
    status_code=201,
    responses={
        200: {"model": AssessmentCreatedOut, "description": "Idempotent replay"},
        **_AUTH_PROBLEM_RESPONSES,
        403: {
            "description": "Only patients submit assessments, and only their own",
            "content": _PROBLEM_CONTENT,
        },
        404: {"description": "Scale is unavailable", "content": _PROBLEM_CONTENT},
        409: {
            "description": "Submission conflict, stale scale version or no lead doctor",
            "content": _PROBLEM_CONTENT,
        },
        422: {
            "description": "Application or request validation problem",
            "content": _VALIDATION_PROBLEM_CONTENT,
        },
        503: _DEFINITION_CONFLICT_RESPONSE,
    },
)
async def submit_assessment(
    body: AssessmentCreateIn,
    response: Response,
    patient: CurrentUser,
    session: DbSession,
    catalog: Catalog,
    rules: AlertRules,
) -> AssessmentCreatedOut | JSONResponse:
    """Принять результат один раз и вернуть сохранённый снимок шкалы и признак сигнала."""
    if patient.role is not Role.PATIENT:
        return problem(403, "Only patients submit assessments", code="patient_mismatch")
    try:
        result, created = await service.create_result(
            session, catalog, rules, patient_id=patient.id, body=body
        )
    except service.AssessmentError as error:
        return _problem(error)
    await session.commit()
    response.status_code = 201 if created else 200
    return result


@router.get("", response_model=AssessmentHistoryOut, responses=_AUTH_PROBLEM_RESPONSES)
async def list_own_assessments(
    patient: PatientUser, session: DbSession, query: History
) -> AssessmentHistoryOut:
    """История текущего пациента; чтение записывается в журнал доступа."""
    history = await service.list_own(session, patient, query)
    await session.commit()
    return history


@patient_history_router.get(
    "/patients/{patient_id}/assessments",
    response_model=AssessmentHistoryOut,
    responses={
        **_AUTH_PROBLEM_RESPONSES,
        404: {"description": "Patient not found", "content": _PROBLEM_CONTENT},
    },
)
async def list_patient_assessments(
    patient: Annotated[PatientSummary, Depends(owned_patient("assessments.list", deputy=True))],
    session: DbSession,
    query: History,
) -> AssessmentHistoryOut:
    """История пациента для ведущего врача с проверкой владения и аудитом."""
    return await service.list_for_owned_patient(session, patient, query)


@patient_history_router.post(
    "/patients/{patient_id}/assessments",
    response_model=AssessmentOut,
    status_code=201,
    responses={
        200: {"model": AssessmentOut, "description": "Idempotent replay"},
        **_AUTH_PROBLEM_RESPONSES,
        404: {"description": "Patient not found or scale unavailable", "content": _PROBLEM_CONTENT},
        409: {
            "description": "Submission conflict, stale scale version or no lead doctor",
            "content": _PROBLEM_CONTENT,
        },
        422: {
            "description": "Application or request validation problem",
            "content": _VALIDATION_PROBLEM_CONTENT,
        },
        503: _DEFINITION_CONFLICT_RESPONSE,
    },
)
async def record_patient_assessment(
    body: ClinicianAssessmentIn,
    response: Response,
    patient: Annotated[PatientSummary, Depends(owned_patient("assessments.create"))],
    doctor: DoctorUser,
    session: DbSession,
    catalog: Catalog,
    rules: AlertRules,
) -> AssessmentOut | JSONResponse:
    """Врач вносит результат шкалы врача за своего пациента (#94); повтор ключа — 200."""
    try:
        result, created = await service.create_for_patient(
            session, catalog, rules, patient=patient, doctor=doctor, body=body
        )
    except service.AssessmentError as error:
        return _problem(error)
    await session.commit()
    response.status_code = 201 if created else 200
    return result


@patient_history_router.get(
    "/patients/{patient_id}/assessments/schedule",
    response_model=PatientScheduleOut,
    responses={
        **_AUTH_PROBLEM_RESPONSES,
        404: {"description": "Patient not found", "content": _PROBLEM_CONTENT},
    },
)
async def get_patient_schedule(
    patient: Annotated[PatientSummary, Depends(owned_patient("assessments.schedule", deputy=True))],
    session: DbSession,
    catalog: Catalog,
    settings: AppSettings,
    scale_id: Annotated[str | None, Query(min_length=1)] = None,
) -> PatientScheduleOut:
    """Плановые даты шкал пациента для ведущего врача; пропуск — дата со статусом missed."""
    return await schedule.patient_schedule(
        session, catalog, patient, settings.timezone, scale_id=scale_id
    )
