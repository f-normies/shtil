"""HTTP-роутер модуля alerts: сигналы врача и пациента, доставка, действия врача (спека #95)."""

from typing import Annotated, Literal, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from clin_compass.core.deps import DbSession
from clin_compass.modules.alerts import service
from clin_compass.modules.alerts.deps import AlertRules
from clin_compass.modules.alerts.help import patient_help_view
from clin_compass.modules.alerts.schemas import (
    AlertOut,
    DecisionsOut,
    DeliveryChannel,
    PatientHelpOut,
    ReferralIn,
    ReferralOut,
    ResolveIn,
    SnoozeIn,
    ThresholdOut,
)
from clin_compass.modules.identity import service as identity
from clin_compass.modules.identity.deps import DoctorUser, PatientUser
from clin_compass.modules.patients import service as patients
from clin_compass.modules.patients.deps import owned_patient
from clin_compass.modules.patients.schemas import PatientSummary
from clin_compass.modules.proposals import service as proposals

router = APIRouter(tags=["alerts"])


@router.get("/alerts")
async def list_alerts(
    doctor: DoctorUser,
    session: DbSession,
    status: Literal["open", "resolved"] = "open",
    patient_id: UUID | None = None,
) -> list[AlertOut]:
    """Сигналы врача: открытые (новые, в работе, отложенные) или решённые.

    `patient_id` — отбор по своему пациенту; чужой или несуществующий — пустой список и запись
    об отказе. Кабинет запрашивает открытые и после подключения к WebSocket. С ними — сигналы,
    переданные врачу как заместителю (спека #56).
    """
    action = f"alerts.list_{status}"
    if patient_id is not None and not await _may_see(session, doctor.id, patient_id):
        await identity.log_access(
            session, actor_id=doctor.id, action=f"{action}.denied", subject=str(patient_id)
        )
        await session.commit()
        return []
    found = await service.list_alerts(
        session, doctor_id=doctor.id, status=status, patient_id=patient_id
    )
    # В ответе данные нескольких пациентов: в журнал — по записи на каждого. Пустой список —
    # одна запись: о запрошенном пациенте или без пациента.
    patient_ids: list[UUID | None] = [*sorted({a.patient_id for a in found})] or [patient_id]
    # Сигналы пациента, переданные врачу как заместителю, — запись с `.deputy` (спека #56).
    as_deputy = {a.patient_id for a in found if a.as_deputy}
    for one in patient_ids or [None]:
        logged = f"{action}.deputy" if one in as_deputy else action
        await identity.log_access(session, actor_id=doctor.id, action=logged, patient_id=one)
    await session.commit()
    return found


@router.get("/alerts/decisions")
async def decisions(_doctor: DoctorUser, rules: AlertRules) -> DecisionsOut:
    """Варианты решений по видам сигнала и пределы отсрочки (спека #95)."""
    return service.decisions_view(rules.decisions)


@router.get("/patient-help")
async def patient_help(_patient: PatientUser, rules: AlertRules) -> PatientHelpOut:
    """Экран помощи пациенту: текст, номера и условия правил `kind: alert` (спека #57).

    Данных пациента в ответе нет, журнал доступа не пишется. PWA хранит ответ у себя
    и открывает экран без сети.
    """
    view = patient_help_view(rules)
    if view is None:
        raise HTTPException(status_code=404, detail="Patient help is unavailable")
    return view


@router.get("/alerts/thresholds")
async def thresholds(scale: str, _doctor: DoctorUser, rules: AlertRules) -> list[ThresholdOut]:
    """Пороги правил по шкале: линейка и шкала ответа в блоке «Причина»."""
    return service.thresholds(rules.rules, scale)


@router.get("/patients/{patient_id}/alerts")
async def list_patient_alerts(
    patient: Annotated[PatientSummary, Depends(owned_patient("patient.alerts.list", deputy=True))],
    doctor: DoctorUser,
    session: DbSession,
) -> list[AlertOut]:
    """Все сигналы пациента с историей срабатываний для ведущего врача. Заместителю — только
    сигналы, по которым у него доступ (спека #56, 9.1)."""
    return await service.list_for_patient(
        session, patient_id=patient.id, viewer_id=doctor.id, deputy=patient.access == "deputy"
    )


@router.post("/alerts/{alert_id}/delivered", status_code=204)
async def delivered(
    alert_id: UUID, via: DeliveryChannel, doctor: DoctorUser, session: DbSession
) -> Response:
    """Кабинет получил сигнал: `ws` — по событию WebSocket, `rest` — дочитал после подключения."""
    try:
        await service.mark_delivered(session, doctor_id=doctor.id, alert_id=alert_id, via=via)
    except service.AlertNotFoundError:
        await _deny(session, doctor.id, "alert.delivered.denied", alert_id)
    await session.commit()
    return Response(status_code=204)


@router.post("/alerts/{alert_id}/acknowledge")
async def acknowledge(alert_id: UUID, doctor: DoctorUser, session: DbSession) -> AlertOut:
    """Взять в работу: новый или отложенный сигнал → в работе."""
    try:
        alert = await service.acknowledge(session, doctor_id=doctor.id, alert_id=alert_id)
    except service.AlertNotFoundError:
        await _deny(session, doctor.id, "alert.acknowledge.denied", alert_id)
    return await _done(session, doctor.id, "alert.acknowledge", alert)


@router.post("/alerts/{alert_id}/snooze")
async def snooze(
    alert_id: UUID, body: SnoozeIn, doctor: DoctorUser, session: DbSession, rules: AlertRules
) -> AlertOut:
    """Отложить «Желательно» до даты. «Обязательно» — 409, дата вне пределов — 422."""
    try:
        alert = await service.snooze(
            session, rules.decisions, doctor_id=doctor.id, alert_id=alert_id, until=body.until
        )
    except service.AlertNotFoundError:
        await _deny(session, doctor.id, "alert.snooze.denied", alert_id)
    except (service.ConflictError, service.InvalidError) as error:
        await _reject(session, error)
    return await _done(session, doctor.id, "alert.snooze", alert)


@router.post("/alerts/{alert_id}/no-contact")
async def no_contact(alert_id: UUID, doctor: DoctorUser, session: DbSession) -> AlertOut:
    """«Не удалось связаться»: запись в истории, сигнал остаётся в работе."""
    try:
        alert = await service.no_contact(session, doctor_id=doctor.id, alert_id=alert_id)
    except service.AlertNotFoundError:
        await _deny(session, doctor.id, "alert.no_contact.denied", alert_id)
    except service.ConflictError as error:
        await _reject(session, error)
    return await _done(session, doctor.id, "alert.no_contact", alert)


@router.post("/alerts/{alert_id}/resolve")
async def resolve(
    alert_id: UUID, body: ResolveIn, doctor: DoctorUser, session: DbSession, rules: AlertRules
) -> AlertOut:
    """Решение: вариант и причина. Без причины у «Обязательно» — 422, решённый — 409."""
    try:
        alert = await service.resolve(
            session,
            rules.decisions,
            doctor_id=doctor.id,
            alert_id=alert_id,
            decision_id=body.decision_id,
            reason=body.reason,
        )
    except service.AlertNotFoundError:
        await _deny(session, doctor.id, "alert.resolve.denied", alert_id)
    except (service.ConflictError, service.InvalidError) as error:
        await _reject(session, error)
    return await _done(session, doctor.id, "alert.resolve", alert)


@router.post("/patients/{patient_id}/referral")
async def referral(
    body: ReferralIn,
    patient: Annotated[PatientSummary, Depends(owned_patient("patient.referral.decide"))],
    doctor: DoctorUser,
    session: DbSession,
    rules: AlertRules,
    request: Request,
) -> ReferralOut:
    """Решение по направлению к психиатру (спека #35): подтвердить, отклонить или отложить.

    Подтверждение и отказ решают предложение и все нерешённые сигналы маршрутизации
    пациента. `alert_ids` не совпали с ними или решать нечего — 409.
    """
    registry: proposals.ApplierRegistry = request.app.state.proposal_appliers
    try:
        result = await service.decide_referral(
            session,
            registry,
            rules.decisions,
            doctor_id=doctor.id,
            patient_id=patient.id,
            body=body,
        )
    except (service.ConflictError, service.InvalidError) as error:
        await _reject(session, error)
    except proposals.ProposalAlreadyDecidedError:
        await _reject(session, service.ConflictError("Сигнал уже рассмотрен"))
    await identity.log_access(
        session, actor_id=doctor.id, action=f"referral.{body.decision}", patient_id=patient.id
    )
    await session.commit()
    return result


async def _may_see(session: AsyncSession, doctor_id: UUID, patient_id: UUID) -> bool:
    """Ведущий врач пациента или заместитель с действующим доступом к нему."""
    if await patients.lead_doctor_of(session, patient_id) == doctor_id:
        return True
    return bool(await patients.granted_alerts(session, doctor_id=doctor_id, patient_id=patient_id))


async def _done(session: AsyncSession, doctor_id: UUID, action: str, alert: AlertOut) -> AlertOut:
    """Действие удалось: запись в журнал доступа и коммит. Заместитель — с суффиксом `.deputy`."""
    await identity.log_access(
        session,
        actor_id=doctor_id,
        action=f"{action}.deputy" if alert.as_deputy else action,
        patient_id=alert.patient_id,
        subject=str(alert.id),
    )
    await session.commit()
    return alert


async def _reject(
    session: AsyncSession, error: "service.ConflictError | service.InvalidError"
) -> NoReturn:
    """Действие невозможно (409) или данные неверны (422). Изменений нет: откат."""
    await session.rollback()
    status = 409 if isinstance(error, service.ConflictError) else 422
    raise HTTPException(status_code=status, detail=str(error)) from None


async def _deny(session: AsyncSession, doctor_id: UUID, action: str, alert_id: UUID) -> NoReturn:
    """Записать отказ в журнал доступа и ответить 404.

    Как в `owned_patient`: попытка учитывается сразу. Ответ не раскрывает, есть ли сигнал,
    поэтому пациент сигнала в запись не попадает.
    """
    await identity.log_access(session, actor_id=doctor_id, action=action, subject=str(alert_id))
    await session.commit()
    raise HTTPException(status_code=404, detail="Alert not found") from None
