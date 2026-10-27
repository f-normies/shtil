"""HTTP-роутер модуля proposals: предложения пациента, очередь врача и его решение."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import AwareDatetime

from clin_compass.core.deps import DbSession
from clin_compass.modules.identity import service as identity
from clin_compass.modules.identity.deps import DoctorUser
from clin_compass.modules.patients.deps import owned_patient
from clin_compass.modules.patients.schemas import PatientSummary
from clin_compass.modules.proposals import service
from clin_compass.modules.proposals.schemas import (
    DecisionRequest,
    ProposalOut,
    ProposalStatus,
    QueuePage,
    QueueSummary,
)

router = APIRouter(tags=["proposals"])


@router.get("/proposals")
async def list_queue(
    doctor: DoctorUser,
    session: DbSession,
    status: ProposalStatus = "proposed",
    q: Annotated[str | None, Query(max_length=200)] = None,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> QueuePage:
    """Предложения всех пациентов врача по статусу: страницы по курсору, поиск по ФИО (#96)."""
    try:
        found = await service.list_for_doctor(
            session, doctor_id=doctor.id, status=status, q=q, cursor=cursor, limit=limit
        )
    except service.InvalidCursorError:
        raise HTTPException(status_code=422, detail="Invalid cursor") from None
    # В ответе данные нескольких пациентов: в журнал — по записи на каждого, как у GET /alerts.
    # Пустой ответ — одна запись без пациента.
    patient_ids: list[UUID | None] = [*sorted({i.patient_id for i in found.items})] or [None]
    for one in patient_ids:
        await identity.log_access(
            session, actor_id=doctor.id, action=f"proposals.list_{status}", patient_id=one
        )
    await session.commit()
    return found


@router.get("/proposals/summary")
async def queue_summary(
    doctor: DoctorUser, session: DbSession, since: AwareDatetime
) -> QueueSummary:
    """Ждут решения и решены с `since` — начала дня врача в его часовом поясе (#96)."""
    return await service.summary_for_doctor(session, doctor_id=doctor.id, since=since)


@router.get("/patients/{patient_id}/proposals")
async def list_patient_proposals(
    patient: Annotated[PatientSummary, Depends(owned_patient("patient.proposals.list"))],
    session: DbSession,
) -> list[ProposalOut]:
    return await service.list_for_patient(session, patient_id=patient.id)


async def _decide(
    request: Request,
    session: DbSession,
    doctor_id: UUID,
    proposal_id: UUID,
    body: DecisionRequest,
    *,
    confirm: bool,
) -> ProposalOut:
    registry: service.ApplierRegistry = request.app.state.proposal_appliers
    try:
        decided = await service.decide(
            session,
            registry,
            doctor_id=doctor_id,
            proposal_id=proposal_id,
            confirm=confirm,
            comment=body.comment,
            common_route=True,
        )
    except service.ProposalNotFoundError:
        raise HTTPException(status_code=404, detail="Proposal not found") from None
    except service.ProposalAlreadyDecidedError:
        raise HTTPException(status_code=409, detail="Proposal is already decided") from None
    except service.DecidedInModuleError:
        raise HTTPException(
            status_code=409, detail="Решение по направлению — на экране сигнала"
        ) from None
    except service.NoApplierError:
        await session.rollback()
        raise HTTPException(status_code=409, detail="Proposal kind cannot be applied") from None
    except service.ProposalApplyError as error:
        await session.rollback()
        raise HTTPException(status_code=422, detail=str(error)) from None
    await identity.log_access(
        session,
        actor_id=doctor_id,
        action="proposal.confirm" if confirm else "proposal.reject",
        patient_id=decided.patient_id,
        subject=str(decided.id),
    )
    await session.commit()
    return decided


@router.post("/proposals/{proposal_id}/confirm")
async def confirm(
    proposal_id: UUID,
    body: DecisionRequest,
    doctor: DoctorUser,
    request: Request,
    session: DbSession,
) -> ProposalOut:
    return await _decide(request, session, doctor.id, proposal_id, body, confirm=True)


@router.post("/proposals/{proposal_id}/reject")
async def reject(
    proposal_id: UUID,
    body: DecisionRequest,
    doctor: DoctorUser,
    request: Request,
    session: DbSession,
) -> ProposalOut:
    return await _decide(request, session, doctor.id, proposal_id, body, confirm=False)
