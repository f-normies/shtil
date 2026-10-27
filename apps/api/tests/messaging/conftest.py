"""Синтетические пары врача и пациента для HTTP-тестов чата."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.modules.identity.schemas import UserOut
from clin_compass.modules.patients import service as patients
from tests.conftest import MakeUser
from tests.live import PASSWORD


@dataclass(frozen=True)
class MessagingCare:
    doctor: UserOut
    patient: UserOut


MakeMessagingCare = Callable[[str, str], Awaitable[MessagingCare]]


@pytest.fixture
def make_messaging_care(
    sessionmaker: async_sessionmaker[AsyncSession], make_user: MakeUser
) -> MakeMessagingCare:
    async def create(doctor_email: str, patient_email: str) -> MessagingCare:
        doctor = await make_user(email=doctor_email)
        async with sessionmaker() as session:
            invite = await patients.create_invite(session, doctor.id, timedelta(days=1))
            patient = await patients.register_patient(
                session,
                invite_token=invite.token,
                email=patient_email,
                password=PASSWORD,
                full_name="Синтетический пациент",
                consent_version=patients.get_consent_document().version,
            )
            await session.commit()
        return MessagingCare(doctor=doctor, patient=patient)

    return create


async def login(client: httpx.AsyncClient, email: str) -> None:
    client.cookies.clear()
    response = await client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200
