"""Публичные зависимости модуля patients: доступ врача к данным своего пациента.

Любой эндпоинт с `patient_id` в пути берёт пациента через owned_patient: зависимость
проверяет, что врач — ведущий, и пишет обращение в журнал доступа. Отказ тоже пишется
(действие с суффиксом `.denied`) и отвечает 404, не раскрывая существование пациента.

`deputy=True` — ручка минимального набора (спека #56): её читает и заместитель с действующим
доступом по сигналу. Его обращение пишется действием с суффиксом `.deputy`.
"""

from collections.abc import Awaitable, Callable
from uuid import UUID

from fastapi import HTTPException

from clin_compass.core.deps import DbSession
from clin_compass.modules.identity import service as identity
from clin_compass.modules.identity.deps import DoctorUser
from clin_compass.modules.patients import service
from clin_compass.modules.patients.schemas import PatientSummary


def owned_patient(action: str, *, deputy: bool = False) -> Callable[..., Awaitable[PatientSummary]]:
    async def dependency(
        patient_id: UUID, doctor: DoctorUser, session: DbSession
    ) -> PatientSummary:
        patient = await service.get_patient_for_doctor(
            session, doctor_id=doctor.id, patient_id=patient_id, deputy=deputy
        )
        if patient is None:
            logged_action = f"{action}.denied"
        elif patient.access == "deputy":
            logged_action = f"{action}.deputy"
        else:
            logged_action = action
        await identity.log_access(
            session, actor_id=doctor.id, action=logged_action, patient_id=patient_id
        )
        # Запись в журнал фиксируем сразу: попытка доступа учитывается, даже если
        # обработчик дальше упадёт.
        await session.commit()
        if patient is None:
            raise HTTPException(status_code=404, detail="Patient not found")
        return patient

    return dependency
