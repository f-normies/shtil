"""HTTP-роутер модуля summary: сводка, подтверждение, повтор (спека #38, 3.5).

Ошибки — RFC 9457 с `code` на верхнем уровне тела: по нему кабинет различает случаи.
"""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from clin_compass.core.deps import AppSettings, DbSession
from clin_compass.core.errors import PROBLEM_JSON, ProblemOut, problem
from clin_compass.modules.identity.deps import DoctorUser
from clin_compass.modules.patients.deps import owned_patient
from clin_compass.modules.patients.schemas import PatientSummary
from clin_compass.modules.summary import service
from clin_compass.modules.summary.schemas import ConfirmSummaryIn, VisitSummaryOut

router = APIRouter(tags=["summary"])

_PROBLEM = {PROBLEM_JSON: {"schema": ProblemOut.model_json_schema()}}
_NOT_FOUND = "Summary not found"
_CONFIRM_ERRORS: dict[int | str, dict[str, Any]] = {
    404: {"description": "Summary not found", "content": _PROBLEM},
    409: {"description": "already_confirmed, stale", "content": _PROBLEM},
    422: {
        "description": "unknown_fact, too_many_statements or invalid statements",
        "content": _PROBLEM,
    },
}
_RETRY_ERRORS: dict[int | str, dict[str, Any]] = {
    409: {"description": "not_failed", "content": _PROBLEM},
}


@router.get("/patients/{patient_id}/summary")
async def get_summary(
    patient: Annotated[PatientSummary, Depends(owned_patient("patient.summary"))],
    session: DbSession,
    settings: AppSettings,
) -> VisitSummaryOut:
    """Сводка с прошлого приёма. Черновика по текущим данным нет — ставит расчёт (`pending`)."""
    result = await service.get_summary(session, patient, settings.timezone)
    await session.commit()
    return result


@router.post(
    "/patients/{patient_id}/summary/{summary_id}/confirm",
    response_model=VisitSummaryOut,
    responses=_CONFIRM_ERRORS,
)
async def confirm_summary(
    summary_id: UUID,
    body: ConfirmSummaryIn,
    patient: Annotated[PatientSummary, Depends(owned_patient("patient.summary.confirm"))],
    doctor: DoctorUser,
    session: DbSession,
    settings: AppSettings,
) -> VisitSummaryOut | JSONResponse:
    """Подтвердить черновик версией врача: текст можно изменить, утверждения — убрать.

    Утверждений не больше, чем в черновике; номера фактов — из тех, на которые он ссылается.
    """
    try:
        result = await service.confirm(
            session,
            patient,
            settings.timezone,
            summary_id=summary_id,
            doctor_id=doctor.id,
            statements=body.statements,
        )
    except service.SummaryNotFoundError:
        await session.rollback()
        return problem(404, _NOT_FOUND)
    except service.SummaryConflictError as error:
        await session.rollback()
        return problem(409, error.detail, code=error.code)
    except service.InvalidVersionError as error:
        await session.rollback()
        return problem(422, error.detail, code=error.code)
    await session.commit()
    return result


@router.post(
    "/patients/{patient_id}/summary/retry",
    response_model=VisitSummaryOut,
    responses=_RETRY_ERRORS,
)
async def retry_summary(
    patient: Annotated[PatientSummary, Depends(owned_patient("patient.summary.retry"))],
    session: DbSession,
    settings: AppSettings,
) -> VisitSummaryOut | JSONResponse:
    """Сводку, которую составить не удалось, — снова в расчёт. Не `failed` — 409."""
    try:
        result = await service.retry(session, patient, settings.timezone)
    except service.SummaryConflictError as error:
        await session.rollback()
        return problem(409, error.detail, code=error.code)
    await session.commit()
    return result
