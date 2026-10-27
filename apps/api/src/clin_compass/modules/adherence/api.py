"""HTTP-роутер модуля adherence: отметки доз пациентом и приём для врача (спека #34)."""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from clin_compass.core.deps import DbSession
from clin_compass.modules.adherence import service
from clin_compass.modules.adherence.schemas import AdherenceOut, DayDosesOut, MarksIn
from clin_compass.modules.identity import service as identity
from clin_compass.modules.identity.deps import PatientUser
from clin_compass.modules.patients.deps import owned_patient
from clin_compass.modules.patients.schemas import PatientSummary

router = APIRouter(tags=["adherence"])


@router.get("/me/doses")
async def my_doses(
    patient: PatientUser, session: DbSession, day: date | None = None
) -> DayDosesOut:
    """Дозы дня вошедшего пациента; без `day` — сегодня по Москве."""
    await identity.log_access(
        session, actor_id=patient.id, action="doses.own", patient_id=patient.id
    )
    now = service.now()
    result = await service.day_doses(session, patient.id, day or now.date(), now)
    await session.commit()
    return result


@router.put("/me/doses")
async def mark_doses(body: MarksIn, patient: PatientUser, session: DbSession) -> DayDosesOut:
    """Отметки доз одной транзакцией. Ответ — дозы дня первой отметки."""
    now = service.now()
    try:
        await service.mark(session, patient.id, body.marks, now.date())
    except service.DoseNotFoundError:
        await session.rollback()
        raise HTTPException(status_code=404, detail="Prescription not found") from None
    except service.InvalidDoseError as error:
        await session.rollback()
        raise HTTPException(status_code=422, detail=str(error)) from None
    await identity.log_access(
        session, actor_id=patient.id, action="doses.mark", patient_id=patient.id
    )
    result = await service.day_doses(session, patient.id, body.marks[0].day, now)
    await session.commit()
    return result


@router.get("/patients/{patient_id}/adherence")
async def patient_adherence(
    patient: Annotated[PatientSummary, Depends(owned_patient("patient.adherence.view"))],
    session: DbSession,
) -> AdherenceOut:
    """Приём за 14 дней по отметкам пациента."""
    result = await service.summary(session, patient.id, service.now())
    await session.commit()
    return result
