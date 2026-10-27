"""Телефон пациента: правит только ведущий врач, формат E.164 (спека #13, раздел 5.1)."""

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.modules.patients.schemas import PhoneUpdate
from tests.conftest import MakeUser
from tests.patients.test_access import login, register_patient


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("+79000000001", "+79000000001"),
        ("+7 (900) 000-00-01", "+79000000001"),
        ("8 900 000 00 01", "+79000000001"),
        ("+4915112345678", "+4915112345678"),
        (None, None),
    ],
)
def test_phone_is_normalized(raw: str | None, expected: str | None) -> None:
    assert PhoneUpdate(phone=raw).phone == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "9000000001",
        "+0123456789",
        "+7900",
        "+7900000000012345",
        "+7 900 abc 00 01",
        # Цифры не из ASCII: `tel:` с ними не набирается.
        "+7٩٠٠٠٠٠٠٠٠١",
    ],
)
def test_invalid_phone_is_rejected(raw: str) -> None:
    with pytest.raises(ValidationError):
        PhoneUpdate(phone=raw)


async def test_lead_doctor_sets_and_clears_phone(
    client: httpx.AsyncClient, make_user: MakeUser
) -> None:
    await make_user(email="a@example.com")
    patient_id = await register_patient(client, "a@example.com", "pa@example.com")
    await login(client, "a@example.com")

    created = await client.get(f"/api/v1/patients/{patient_id}")
    assert created.json()["phone"] is None

    updated = await client.put(
        f"/api/v1/patients/{patient_id}/phone", json={"phone": "8 900 000-00-01"}
    )
    assert updated.status_code == 200
    assert updated.json()["phone"] == "+79000000001"
    assert (await client.get(f"/api/v1/patients/{patient_id}")).json()["phone"] == "+79000000001"
    assert (await client.get("/api/v1/patients")).json()[0]["phone"] == "+79000000001"

    cleared = await client.put(f"/api/v1/patients/{patient_id}/phone", json={"phone": None})
    assert cleared.json()["phone"] is None


async def test_invalid_phone_returns_422(client: httpx.AsyncClient, make_user: MakeUser) -> None:
    await make_user(email="a@example.com")
    patient_id = await register_patient(client, "a@example.com", "pa@example.com")
    await login(client, "a@example.com")

    response = await client.put(f"/api/v1/patients/{patient_id}/phone", json={"phone": "12345"})

    assert response.status_code == 422


async def test_other_doctor_cannot_change_phone(
    client: httpx.AsyncClient,
    make_user: MakeUser,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await make_user(email="a@example.com")
    stranger = await make_user(email="b@example.com")
    patient_id = await register_patient(client, "a@example.com", "pa@example.com")
    await login(client, "b@example.com")

    response = await client.put(
        f"/api/v1/patients/{patient_id}/phone", json={"phone": "+79000000001"}
    )

    assert response.status_code == 404
    async with sessionmaker() as session:
        rows = (
            await session.execute(
                text("SELECT actor_id, action, patient_id FROM identity.access_log")
            )
        ).all()
    assert rows == [(stranger.id, "patient.phone.update.denied", patient_id)]


async def test_phone_update_is_written_to_access_log_once(
    client: httpx.AsyncClient,
    make_user: MakeUser,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    doctor = await make_user(email="a@example.com")
    patient_id = await register_patient(client, "a@example.com", "pa@example.com")
    await login(client, "a@example.com")

    await client.put(f"/api/v1/patients/{patient_id}/phone", json={"phone": "+79000000001"})

    async with sessionmaker() as session:
        rows = (
            await session.execute(
                text("SELECT actor_id, action, patient_id FROM identity.access_log")
            )
        ).all()
    assert rows == [(doctor.id, "patient.phone.update", patient_id)]


async def test_patient_cannot_change_phone(client: httpx.AsyncClient, make_user: MakeUser) -> None:
    await make_user(email="a@example.com")
    patient_id = await register_patient(client, "a@example.com", "pa@example.com")
    await login(client, "pa@example.com")

    response = await client.put(
        f"/api/v1/patients/{patient_id}/phone", json={"phone": "+79000000001"}
    )

    assert response.status_code == 403
