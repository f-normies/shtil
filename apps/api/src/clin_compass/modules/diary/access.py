"""Владение пациентом и журналирование чтений дневника."""

import contextlib
from typing import NoReturn
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from clin_compass.modules.diary.errors import DiaryError
from clin_compass.modules.diary.models import Entry
from clin_compass.modules.identity import service as identity
from clin_compass.modules.identity.schemas import Role, UserOut
from clin_compass.modules.patients import service as patients

log = structlog.get_logger()


async def authorize_self_patient(
    session: AsyncSession,
    actor: UserOut,
    *,
    action: str,
    subject: str | None = None,
    lock_patient: bool = False,
) -> UUID:
    """Проверить роль пациента и его связь с ведущим врачом."""
    if actor.role is not Role.PATIENT:
        await _deny(
            session,
            actor,
            actor.id,
            action,
            403,
            "forbidden",
            "Only patients can use this endpoint",
            subject=subject,
        )
    return await authorize(
        session,
        actor,
        patient_id=actor.id,
        action=action,
        subject=subject,
        lock_patient=lock_patient,
    )


async def authorize(
    session: AsyncSession,
    actor: UserOut,
    *,
    patient_id: UUID,
    action: str,
    subject: str | None = None,
    lock_patient: bool = False,
) -> UUID:
    """Проверить роль и текущее владение; отказ записать отдельной транзакцией."""
    if actor.role not in {Role.PATIENT, Role.DOCTOR}:
        await _deny(
            session,
            actor,
            patient_id,
            action,
            403,
            "forbidden",
            "Role cannot access diary",
            subject=subject,
        )
    if actor.role is Role.PATIENT and actor.id != patient_id:
        await _deny(
            session,
            actor,
            patient_id,
            action,
            404,
            "not_found",
            "Diary entry not found",
            subject=subject,
        )

    lead_doctor_id = (
        await patients.lead_doctor_of_for_update(session, patient_id)
        if lock_patient
        else await patients.lead_doctor_of(session, patient_id)
    )
    if lead_doctor_id is None:
        await _deny(
            session,
            actor,
            patient_id,
            action,
            404,
            "not_found",
            "Diary entry not found",
            subject=subject,
        )
    if actor.role is Role.DOCTOR and lead_doctor_id != actor.id:
        await _deny(
            session,
            actor,
            patient_id,
            action,
            404,
            "not_found",
            "Diary entry not found",
            subject=subject,
        )

    await identity.log_access(
        session,
        actor_id=actor.id,
        action=action,
        patient_id=patient_id,
        subject=subject,
    )
    return lead_doctor_id


async def authorize_doctor(
    session: AsyncSession,
    *,
    doctor_id: UUID,
    patient_id: UUID,
    action: str,
    subject: str | None = None,
    lock_patient: bool = False,
) -> UUID:
    """Публичный сервисный доступ для контрактов, получающих doctor_id отдельно."""
    doctor = await identity.get_user(session, doctor_id)
    if doctor is None:
        raise DiaryError(404, "not_found", "Patient not found")
    if doctor.role is not Role.DOCTOR:
        await _deny(
            session,
            doctor,
            patient_id,
            action,
            403,
            "forbidden",
            "Role cannot access diary",
            subject=subject,
        )
    return await authorize(
        session,
        doctor,
        patient_id=patient_id,
        action=action,
        subject=subject,
        lock_patient=lock_patient,
    )


async def authorize_patient_entry(
    session: AsyncSession,
    actor: UserOut,
    *,
    entry_id: UUID,
    action: str,
    lock_patient: bool = False,
) -> tuple[Entry | None, UUID]:
    """Проверить владельца по ID записи и аудировать попытку чтения чужого entry."""
    if actor.role is not Role.PATIENT:
        await authorize_self_patient(
            session,
            actor,
            action=action,
            subject=str(entry_id),
            lock_patient=lock_patient,
        )
    entry = await session.get(Entry, entry_id)
    owner_id = entry.patient_id if entry is not None else actor.id
    doctor_id = await authorize(
        session,
        actor,
        patient_id=owner_id,
        action=action,
        subject=str(entry_id),
        lock_patient=lock_patient,
    )
    if entry is not None and lock_patient:
        entry = await session.scalar(
            select(Entry)
            .where(Entry.id == entry_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    return entry, doctor_id


async def _deny(
    session: AsyncSession,
    actor: UserOut,
    patient_id: UUID,
    action: str,
    status: int,
    code: str,
    detail: str,
    *,
    subject: str | None = None,
) -> NoReturn:
    """Сохранить аудит отказа вне транзакции бизнес-операции."""
    try:
        await session.rollback()
        await identity.log_access(
            session,
            actor_id=actor.id,
            action=f"{action}.denied",
            patient_id=patient_id,
            subject=subject,
        )
        await session.commit()
    except SQLAlchemyError as error:
        with contextlib.suppress(SQLAlchemyError):
            await session.rollback()
        log.error("diary.audit_failed", error_type=type(error).__name__)
    raise DiaryError(status, code, detail)
