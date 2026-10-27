"""REST-состояние переписки доступно только участнику пары и не кэшируется."""

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.modules.identity.models import AccessLogEntry
from tests.conftest import MakeUser
from tests.messaging.conftest import MakeMessagingCare, MessagingCare, login


async def test_status_returns_current_pair_and_empty_peer_state(
    client: httpx.AsyncClient,
    make_messaging_care: MakeMessagingCare,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    care: MessagingCare = await make_messaging_care("doctor@example.com", "patient@example.com")
    await login(client, care.patient.email)

    response = await client.get(f"/api/v1/patients/{care.patient.id}/messaging-status")

    assert response.status_code == 200
    assert response.headers.get("cache-control") == "no-store"
    assert response.json() == {
        "patient_id": str(care.patient.id),
        "doctor_id": str(care.doctor.id),
        "peer_full_name": care.doctor.full_name,
        "online": False,
        "last_seen_at": None,
        "typing_until": None,
    }
    async with sessionmaker() as session:
        audit = await session.scalar(
            select(AccessLogEntry).where(
                AccessLogEntry.actor_id == care.patient.id,
                AccessLogEntry.action == "messaging.status",
                AccessLogEntry.patient_id == care.patient.id,
            )
        )
    assert audit is not None


async def test_doctor_status_returns_current_patient_name(
    client: httpx.AsyncClient,
    make_messaging_care: MakeMessagingCare,
) -> None:
    care = await make_messaging_care("status-doctor@example.com", "status-patient@example.com")
    await login(client, care.doctor.email)

    response = await client.get(f"/api/v1/patients/{care.patient.id}/messaging-status")

    assert response.status_code == 200
    assert response.json()["peer_full_name"] == care.patient.full_name


async def test_status_hides_a_patient_outside_the_current_pair(
    client: httpx.AsyncClient,
    make_messaging_care: MakeMessagingCare,
    make_user: MakeUser,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    care = await make_messaging_care("doctor@example.com", "patient@example.com")
    stranger = await make_user(email="stranger@example.com")
    await login(client, stranger.email)

    response = await client.get(f"/api/v1/patients/{care.patient.id}/messaging-status")

    assert response.status_code == 404
    assert response.headers.get("cache-control") == "no-store"
    assert response.json()["code"] == "not_found"
    assert care.patient.full_name not in response.text
    async with sessionmaker() as session:
        audit = await session.scalar(
            select(AccessLogEntry).where(
                AccessLogEntry.actor_id == stranger.id,
                AccessLogEntry.action == "messaging.status.denied",
                AccessLogEntry.patient_id == care.patient.id,
            )
        )
    assert audit is not None
