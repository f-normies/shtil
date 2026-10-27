"""Фикстуры синтетических пар врач—пациент для API дневника."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.modules.identity import service as identity
from clin_compass.modules.identity.schemas import Role, UserOut
from clin_compass.modules.patients import service as patients

PASSWORD = "correct horse battery staple"


@dataclass(frozen=True)
class DiaryCare:
    doctor: UserOut
    patient: UserOut


async def create_care(
    sessionmaker: async_sessionmaker[AsyncSession],
    doctor_email: str = "doctor@example.com",
    patient_email: str = "patient@example.com",
) -> DiaryCare:
    async with sessionmaker() as session:
        doctor = await identity.create_user(
            session,
            email=doctor_email,
            password=PASSWORD,
            role=Role.DOCTOR,
            full_name="Ведущий врач",
        )
        invite = await patients.create_invite(session, doctor.id, timedelta(days=1))
        patient = await patients.register_patient(
            session,
            invite_token=invite.token,
            email=patient_email,
            password=PASSWORD,
            full_name="Синтетический пациент",
            consent_version=patients.get_consent_document().version,
            registered_at=datetime.now(UTC) - timedelta(days=6),
        )
        await session.commit()
    return DiaryCare(doctor=doctor, patient=patient)


@pytest.fixture
async def diary_care(sessionmaker: async_sessionmaker[AsyncSession]) -> DiaryCare:
    return await create_care(sessionmaker)


async def login(client: httpx.AsyncClient, email: str) -> None:
    response = await client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200
