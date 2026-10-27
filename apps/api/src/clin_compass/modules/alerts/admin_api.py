"""HTTP-роутер администратора: заместители врачей и разбор эскалаций (спека #56, раздел 5).

Только роль `admin`. Данных пациента в ответах нет. Обращения пишутся в журнал доступа.
"""

from typing import Literal, NoReturn
from uuid import UUID

from fastapi import APIRouter, HTTPException, Response

from clin_compass.core.deps import DbSession
from clin_compass.modules.alerts import admin, deputies
from clin_compass.modules.alerts.escalation import AssignmentConflictError, AssignmentError
from clin_compass.modules.alerts.lifecycle import moscow_today
from clin_compass.modules.alerts.schemas import AdminDoctorOut, DeputyIn, DeputyOut, EscalationOut
from clin_compass.modules.identity import service as identity
from clin_compass.modules.identity.deps import AdminUser

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/doctors")
async def list_doctors(user: AdminUser, session: DbSession) -> list[AdminDoctorOut]:
    """Врачи организации и их заместитель на сегодня; без заместителя — `no_deputy`."""
    result = await deputies.doctors(session, moscow_today())
    await _log(session, user.id, "admin.doctors.list")
    return result


@router.get("/deputies")
async def list_deputies(user: AdminUser, session: DbSession) -> list[DeputyOut]:
    """Действующие и будущие назначения заместителей."""
    result = await deputies.list_current(session, moscow_today())
    await _log(session, user.id, "admin.deputies.list")
    return result


@router.post("/deputies", status_code=201)
async def assign_deputy(body: DeputyIn, user: AdminUser, session: DbSession) -> DeputyOut:
    """Назначить заместителя. Пересечение с действующим назначением врача — 409, неверные
    данные — 422."""
    try:
        result = await deputies.assign(session, user.id, body)
    except AssignmentConflictError as error:
        await _reject(session, 409, error)
    except AssignmentError as error:
        await _reject(session, 422, error)
    await _log(session, user.id, "admin.deputies.assign", str(result.id))
    return result


@router.post("/deputies/{assignment_id}/revoke")
async def revoke_deputy(assignment_id: UUID, user: AdminUser, session: DbSession) -> DeputyOut:
    """Отозвать назначение: доступы заместителя к пациентам врача закрываются."""
    try:
        result = await deputies.revoke(session, user.id, assignment_id)
    except deputies.DeputyNotFoundError:
        await session.rollback()
        raise HTTPException(status_code=404, detail="Assignment not found") from None
    await _log(session, user.id, "admin.deputies.revoke", str(assignment_id))
    return result


@router.get("/escalations")
async def list_escalations(
    user: AdminUser, session: DbSession, status: Literal["open", "all"] = "open"
) -> list[EscalationOut]:
    """Эскалации без данных пациента; `open` — ещё не разобранные."""
    result = await admin.list_escalations(session, status)
    await _log(session, user.id, f"admin.escalations.list_{status}")
    return result


@router.post("/escalations/{alert_id}/review", status_code=204)
async def review_escalation(alert_id: UUID, user: AdminUser, session: DbSession) -> Response:
    """Эскалация разобрана. Повтор ничего не меняет."""
    try:
        await admin.review(session, admin_id=user.id, alert_id=alert_id)
    except admin.EscalationNotFoundError:
        await session.rollback()
        raise HTTPException(status_code=404, detail="Escalation not found") from None
    await _log(session, user.id, "admin.escalations.review", str(alert_id))
    return Response(status_code=204)


async def _log(session: DbSession, admin_id: UUID, action: str, subject: str | None = None) -> None:
    """Запись в журнал доступа и коммит: без пациента, данных пациента здесь нет."""
    await identity.log_access(session, actor_id=admin_id, action=action, subject=subject)
    await session.commit()


async def _reject(session: DbSession, status: int, error: Exception) -> NoReturn:
    await session.rollback()
    raise HTTPException(status_code=status, detail=str(error)) from None
