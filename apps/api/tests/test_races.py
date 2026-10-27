"""Параллельные запросы: блокировки FOR UPDATE не дают использовать приглашение
дважды и решить предложение дважды."""

import asyncio
from uuid import UUID

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.modules.patients import service as patients
from clin_compass.modules.proposals import service as proposals
from clin_compass.modules.proposals.schemas import ProposalOut
from tests.conftest import MakeUser

PASSWORD = "correct horse battery staple"


async def login(client: httpx.AsyncClient, email: str) -> None:
    response = await client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200


def registration(token: str, email: str) -> dict[str, object]:
    return {
        "invite_token": token,
        "email": email,
        "password": PASSWORD,
        "full_name": "Пациент",
        "consent_personal_data": True,
        "consent_version": patients.get_consent_document().version,
    }


async def test_concurrent_registrations_with_one_invite_create_one_patient(
    app: FastAPI, client: httpx.AsyncClient, make_user: MakeUser
) -> None:
    await make_user(email="doctor@example.com")
    await login(client, "doctor@example.com")
    token = (await client.post("/api/v1/patients/invites")).json()["token"]
    client.cookies.clear()

    async def register(email: str) -> int:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            response = await c.post("/api/v1/patients/register", json=registration(token, email))
            return response.status_code

    statuses = await asyncio.gather(register("one@example.com"), register("two@example.com"))

    assert sorted(statuses) == [201, 410]


async def test_concurrent_confirmations_apply_once(
    app: FastAPI,
    client: httpx.AsyncClient,
    make_user: MakeUser,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    applied: list[ProposalOut] = []

    async def record(_: AsyncSession, proposal: ProposalOut) -> None:
        await asyncio.sleep(0.05)  # окно гонки шире
        applied.append(proposal)

    app.state.proposal_appliers.register("test.record", record)
    await make_user(email="doctor@example.com")
    await login(client, "doctor@example.com")
    token = (await client.post("/api/v1/patients/invites")).json()["token"]
    patient_id = UUID(
        (
            await client.post(
                "/api/v1/patients/register", json=registration(token, "p@example.com")
            )
        ).json()["id"]
    )
    client.cookies.clear()
    await login(client, "doctor@example.com")
    async with sessionmaker() as session:
        proposal = await proposals.create_proposal(
            session,
            patient_id=patient_id,
            kind="test.record",
            payload={},
            origin_type="rule",
            origin_ref="r",
        )
        await session.commit()
    cookies = dict(client.cookies)

    async def confirm() -> int:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test", cookies=cookies
        ) as c:
            response = await c.post(f"/api/v1/proposals/{proposal.id}/confirm", json={})
            return response.status_code

    statuses = await asyncio.gather(confirm(), confirm())

    assert sorted(statuses) == [200, 409]
    assert len(applied) == 1
