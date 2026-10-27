"""«Предложение → подтверждение» (ТЗ: система не выполняет клинических действий сама)."""

from typing import Any
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.modules.patients import service as patients
from clin_compass.modules.proposals import service as proposals
from clin_compass.modules.proposals.schemas import ProposalOut
from tests.conftest import MakeUser

PASSWORD = "correct horse battery staple"
KIND = "test.record"


async def login(client: httpx.AsyncClient, email: str) -> None:
    response = await client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200


async def register_patient(
    client: httpx.AsyncClient, doctor_email: str, email: str, full_name: str = "Пациент"
) -> UUID:
    await login(client, doctor_email)
    token = (await client.post("/api/v1/patients/invites")).json()["token"]
    client.cookies.clear()
    response = await client.post(
        "/api/v1/patients/register",
        json={
            "invite_token": token,
            "email": email,
            "password": PASSWORD,
            "full_name": full_name,
            "consent_personal_data": True,
            "consent_version": patients.get_consent_document().version,
        },
    )
    client.cookies.clear()
    return UUID(response.json()["id"])


@pytest.fixture
def applied(app: FastAPI) -> list[ProposalOut]:
    """Регистрирует тестовый applier и возвращает список применённых предложений."""
    calls: list[ProposalOut] = []

    async def record(_: AsyncSession, proposal: ProposalOut) -> None:
        calls.append(proposal)

    app.state.proposal_appliers.register(KIND, record)
    return calls


async def propose(
    sessionmaker: async_sessionmaker[AsyncSession],
    patient_id: UUID,
    kind: str = KIND,
    payload: dict[str, Any] | None = None,
) -> ProposalOut:
    async with sessionmaker() as session:
        proposal = await proposals.create_proposal(
            session,
            patient_id=patient_id,
            kind=kind,
            payload=payload or {"drug": "Авиандр"},
            origin_type="rule",
            origin_ref="test-rule-1",
        )
        await session.commit()
        return proposal


@pytest.fixture
async def patient_of_a(client: httpx.AsyncClient, make_user: MakeUser) -> UUID:
    await make_user(email="a@example.com")
    await make_user(email="b@example.com")
    return await register_patient(client, "a@example.com", "pa@example.com")


async def test_confirm_applies_proposal_once_and_records_decision(
    client: httpx.AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    patient_of_a: UUID,
    applied: list[ProposalOut],
) -> None:
    proposal = await propose(sessionmaker, patient_of_a, payload={"drug": "Авиандр", "dose_mg": 10})
    await login(client, "a@example.com")

    response = await client.post(f"/api/v1/proposals/{proposal.id}/confirm", json={})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "confirmed"
    assert body["decided_at"] is not None
    assert [p.payload for p in applied] == [{"drug": "Авиандр", "dose_mg": 10}]


async def test_reject_does_not_apply(
    client: httpx.AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    patient_of_a: UUID,
    applied: list[ProposalOut],
) -> None:
    proposal = await propose(sessionmaker, patient_of_a)
    await login(client, "a@example.com")

    response = await client.post(
        f"/api/v1/proposals/{proposal.id}/reject", json={"comment": "Противопоказание"}
    )

    assert response.status_code == 200
    assert response.json()["status"] == "rejected"
    assert response.json()["decision_comment"] == "Противопоказание"
    assert applied == []


async def test_decided_proposal_cannot_be_decided_again(
    client: httpx.AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    patient_of_a: UUID,
    applied: list[ProposalOut],
) -> None:
    proposal = await propose(sessionmaker, patient_of_a)
    await login(client, "a@example.com")
    await client.post(f"/api/v1/proposals/{proposal.id}/confirm", json={})

    again = await client.post(f"/api/v1/proposals/{proposal.id}/confirm", json={})

    assert again.status_code == 409
    assert len(applied) == 1


async def test_other_doctor_cannot_decide(
    client: httpx.AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    patient_of_a: UUID,
    applied: list[ProposalOut],
) -> None:
    proposal = await propose(sessionmaker, patient_of_a)
    await login(client, "b@example.com")

    response = await client.post(f"/api/v1/proposals/{proposal.id}/confirm", json={})

    assert response.status_code == 404
    assert applied == []


async def test_patient_cannot_confirm_own_proposal(
    client: httpx.AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    patient_of_a: UUID,
    applied: list[ProposalOut],
) -> None:
    proposal = await propose(sessionmaker, patient_of_a)
    await login(client, "pa@example.com")

    response = await client.post(f"/api/v1/proposals/{proposal.id}/confirm", json={})

    assert response.status_code == 403
    assert applied == []


async def test_failed_apply_leaves_proposal_undecided(
    app: FastAPI,
    client: httpx.AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    patient_of_a: UUID,
) -> None:
    async def refuse(_: AsyncSession, __: ProposalOut) -> None:
        raise proposals.ProposalApplyError("Доза вне инструкции")

    app.state.proposal_appliers.register("test.refuse", refuse)
    proposal = await propose(sessionmaker, patient_of_a, kind="test.refuse")
    await login(client, "a@example.com")

    response = await client.post(f"/api/v1/proposals/{proposal.id}/confirm", json={})
    listed = await client.get(f"/api/v1/patients/{patient_of_a}/proposals")

    assert response.status_code == 422
    assert response.json()["detail"] == "Доза вне инструкции"
    assert [p["status"] for p in listed.json()] == ["proposed"]


async def test_proposal_without_applier_cannot_be_confirmed(
    client: httpx.AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    patient_of_a: UUID,
) -> None:
    proposal = await propose(sessionmaker, patient_of_a, kind="nobody.handles.this")
    await login(client, "a@example.com")

    response = await client.post(f"/api/v1/proposals/{proposal.id}/confirm", json={})

    assert response.status_code == 409


async def test_doctor_lists_patient_proposals_but_not_foreign(
    client: httpx.AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    patient_of_a: UUID,
) -> None:
    await propose(sessionmaker, patient_of_a)
    await login(client, "b@example.com")
    foreign = await client.get(f"/api/v1/patients/{patient_of_a}/proposals")
    client.cookies.clear()
    await login(client, "a@example.com")

    own = await client.get(f"/api/v1/patients/{patient_of_a}/proposals")

    assert foreign.status_code == 404
    assert own.status_code == 200
    assert [(p["kind"], p["origin_ref"]) for p in own.json()] == [(KIND, "test-rule-1")]


async def test_decision_is_written_to_access_log(
    client: httpx.AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    patient_of_a: UUID,
    applied: list[ProposalOut],
) -> None:
    proposal = await propose(sessionmaker, patient_of_a)
    await login(client, "a@example.com")

    await client.post(f"/api/v1/proposals/{proposal.id}/confirm", json={})

    async with sessionmaker() as session:
        rows = (
            await session.execute(
                text("SELECT action, patient_id, subject FROM identity.access_log")
            )
        ).all()
    assert [tuple(r) for r in rows] == [("proposal.confirm", patient_of_a, str(proposal.id))]


def test_registry_knows_kinds_decided_in_their_module() -> None:
    # Спека #35: направление решается на экране сигнала, общие ручки его не решают.
    async def apply(_: AsyncSession, __: ProposalOut) -> None:
        return None

    registry = proposals.ApplierRegistry()
    registry.register("test.own", apply, decided_in_module=True)
    registry.register("test.common", apply)

    assert registry.decided_in_module("test.own")
    assert not registry.decided_in_module("test.common")
    assert not registry.decided_in_module("test.unknown")


async def test_kind_decided_in_its_module_is_not_decided_by_common_routes(
    app: FastAPI,
    client: httpx.AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    patient_of_a: UUID,
) -> None:
    async def apply(_: AsyncSession, __: ProposalOut) -> None:
        return None

    app.state.proposal_appliers.register("test.own", apply, decided_in_module=True)
    proposal = await propose(sessionmaker, patient_of_a, kind="test.own")
    await login(client, "a@example.com")

    confirmed = await client.post(f"/api/v1/proposals/{proposal.id}/confirm", json={})
    rejected = await client.post(f"/api/v1/proposals/{proposal.id}/reject", json={})

    assert [confirmed.status_code, rejected.status_code] == [409, 409]
    [still] = (await client.get(f"/api/v1/patients/{patient_of_a}/proposals")).json()
    assert still["status"] == "proposed"
