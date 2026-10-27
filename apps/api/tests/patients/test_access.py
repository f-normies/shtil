"""Врач видит только своих пациентов; просмотр карточки пишется в журнал доступа."""

from uuid import UUID

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.modules.patients import service as patients
from tests.conftest import MakeUser

PASSWORD = "correct horse battery staple"


async def login(client: httpx.AsyncClient, email: str) -> None:
    response = await client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200


async def register_patient(
    client: httpx.AsyncClient, doctor_email: str, patient_email: str
) -> UUID:
    """Врач приглашает, пациент регистрируется. Возвращает id пациента; cookie очищены."""
    await login(client, doctor_email)
    token = (await client.post("/api/v1/patients/invites")).json()["token"]
    client.cookies.clear()
    response = await client.post(
        "/api/v1/patients/register",
        json={
            "invite_token": token,
            "email": patient_email,
            "password": PASSWORD,
            "full_name": "Пациент Тестовый",
            "consent_personal_data": True,
            "consent_version": patients.get_consent_document().version,
        },
    )
    client.cookies.clear()
    return UUID(response.json()["id"])


async def test_doctor_lists_only_own_patients(
    client: httpx.AsyncClient, make_user: MakeUser
) -> None:
    await make_user(email="a@example.com")
    await make_user(email="b@example.com")
    await register_patient(client, "a@example.com", "pa@example.com")
    await register_patient(client, "b@example.com", "pb@example.com")
    await login(client, "a@example.com")

    response = await client.get("/api/v1/patients")

    assert response.status_code == 200
    assert [p["email"] for p in response.json()] == ["pa@example.com"]


async def test_doctor_cannot_open_other_doctors_patient(
    client: httpx.AsyncClient, make_user: MakeUser
) -> None:
    await make_user(email="a@example.com")
    await make_user(email="b@example.com")
    foreign = await register_patient(client, "b@example.com", "pb@example.com")
    await login(client, "a@example.com")

    response = await client.get(f"/api/v1/patients/{foreign}")

    # 404, а не 403: чужой врач не должен узнать даже о существовании пациента.
    assert response.status_code == 404


async def test_patient_cannot_list_patients(client: httpx.AsyncClient, make_user: MakeUser) -> None:
    await make_user(email="a@example.com")
    await register_patient(client, "a@example.com", "pa@example.com")
    await login(client, "pa@example.com")

    response = await client.get("/api/v1/patients")

    assert response.status_code == 403


async def test_viewing_patient_card_is_written_to_access_log(
    client: httpx.AsyncClient,
    make_user: MakeUser,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    doctor = await make_user(email="a@example.com")
    patient_id = await register_patient(client, "a@example.com", "pa@example.com")
    await login(client, "a@example.com")

    response = await client.get(f"/api/v1/patients/{patient_id}")

    assert response.status_code == 200
    assert response.json()["id"] == str(patient_id)
    async with sessionmaker() as session:
        rows = (
            await session.execute(
                text("SELECT actor_id, action, patient_id FROM identity.access_log")
            )
        ).all()
    assert rows == [(doctor.id, "patient.view", patient_id)]


async def test_access_log_rejects_updates(
    make_user: MakeUser, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    doctor = await make_user()
    async with sessionmaker() as session:
        await session.execute(
            text("INSERT INTO identity.access_log (actor_id, action) VALUES (:a, 'x')"),
            {"a": doctor.id},
        )
        await session.commit()

    async with sessionmaker() as session:
        with pytest.raises(DBAPIError, match="append-only"):
            await session.execute(text("UPDATE identity.access_log SET action = 'y'"))


async def access_log(maker: async_sessionmaker[AsyncSession]) -> list[tuple[object, ...]]:
    async with maker() as session:
        rows = await session.execute(
            text("SELECT actor_id, action, patient_id FROM identity.access_log ORDER BY id")
        )
        return [tuple(row) for row in rows]


async def test_denied_access_to_foreign_patient_is_logged(
    client: httpx.AsyncClient,
    make_user: MakeUser,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    doctor_a = await make_user(email="a@example.com")
    await make_user(email="b@example.com")
    foreign = await register_patient(client, "b@example.com", "pb@example.com")
    await login(client, "a@example.com")

    response = await client.get(f"/api/v1/patients/{foreign}")

    assert response.status_code == 404
    assert await access_log(sessionmaker) == [(doctor_a.id, "patient.view.denied", foreign)]


async def test_patient_list_and_proposal_list_are_logged(
    client: httpx.AsyncClient,
    make_user: MakeUser,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    doctor = await make_user(email="a@example.com")
    patient_id = await register_patient(client, "a@example.com", "pa@example.com")
    await login(client, "a@example.com")

    await client.get("/api/v1/patients")
    await client.get(f"/api/v1/patients/{patient_id}/proposals")

    assert await access_log(sessionmaker) == [
        (doctor.id, "patients.list", None),
        (doctor.id, "patient.proposals.list", patient_id),
    ]


async def test_access_log_rejects_truncate(
    make_user: MakeUser, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    async with sessionmaker() as session:
        with pytest.raises(DBAPIError, match="append-only"):
            await session.execute(text("TRUNCATE identity.access_log"))
