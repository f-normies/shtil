"""Отзыв согласия пациентом (спека #112, 3.1–3.5, 3.7).

После отзыва канал пациента закрыт: сессии, сокеты, вход. Врач видит отметку и данные.
"""

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

import asyncpg
import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from websockets.asyncio.client import ClientConnection
from websockets.exceptions import ConnectionClosed

from clin_compass.core import tokens
from clin_compass.core.queue import asyncpg_dsn
from clin_compass.core.realtime import CHANNEL
from clin_compass.modules.identity import service as identity
from clin_compass.modules.identity.schemas import Role, UserOut
from clin_compass.modules.patients import service as patients
from tests.live import receive, session_cookie, ws_connect

PASSWORD = "correct horse battery staple"
DOCTOR = "doctor@example.com"
PATIENT = "patient@example.com"
COOKIE = "compass_session"
REVOKE = "/api/v1/me/consent/revoke"


@dataclass(frozen=True)
class Care:
    doctor: UserOut
    patient: UserOut


@pytest.fixture
async def care(sessionmaker: async_sessionmaker[AsyncSession]) -> Care:
    """Врач и пациент, зарегистрированный по его приглашению с согласием."""
    async with sessionmaker() as session:
        doctor = await identity.create_user(
            session, email=DOCTOR, password=PASSWORD, role=Role.DOCTOR, full_name="Врач"
        )
        invite = await patients.create_invite(session, doctor.id, timedelta(days=1))
        patient = await patients.register_patient(
            session,
            invite_token=invite.token,
            email=PATIENT,
            password=PASSWORD,
            full_name="Синтетический пациент",
            consent_version=patients.get_consent_document().version,
        )
        await session.commit()
    return Care(doctor=doctor, patient=patient)


@pytest.fixture
async def notifications(postgres_url: str) -> AsyncIterator[list[dict[str, Any]]]:
    """NOTIFY канала событий, выданные Postgres после коммита."""
    events: list[dict[str, Any]] = []
    connection = await asyncpg.connect(asyncpg_dsn(postgres_url))
    await connection.add_listener(
        CHANNEL, lambda _c, _p, _ch, payload: events.append(json.loads(payload))
    )
    yield events
    await connection.close()


async def closed_with(ws: ClientConnection) -> int | None:
    """Код, с которым сервер закрыл сокет. События до закрытия пропускаются.

    У пациента есть ведущий врач, поэтому до закрытия приходят события присутствия.
    """
    async with asyncio.timeout(5):
        with pytest.raises(ConnectionClosed):
            while True:
                await ws.recv()
    return ws.close_code


async def login(client: httpx.AsyncClient, email: str, password: str = PASSWORD) -> httpx.Response:
    return await client.post("/api/v1/auth/login", json={"email": email, "password": password})


async def logged_in(client: httpx.AsyncClient, email: str) -> str:
    """Войти и вернуть токен сессии из cookie."""
    assert (await login(client, email)).status_code == 200
    return client.cookies[COOKIE]


async def test_revoke_records_revocation_disables_user_and_clears_cookie(
    client: httpx.AsyncClient, care: Care, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    await logged_in(client, PATIENT)

    response = await client.post(REVOKE)
    async with sessionmaker() as session:
        consent = (
            await session.execute(text("SELECT given_at, revoked_at FROM patients.consents"))
        ).one()
        user = (
            await session.execute(
                text("SELECT disabled_at, disabled_reason FROM identity.users WHERE id = :id"),
                {"id": care.patient.id},
            )
        ).one()

    assert response.status_code == 204
    assert 'compass_session=""' in response.headers["set-cookie"]
    assert COOKIE not in client.cookies
    assert consent.revoked_at is not None
    assert consent.revoked_at >= consent.given_at
    assert user.disabled_at is not None
    assert user.disabled_reason == "consent_revoked"


async def test_revoke_ends_every_session_of_the_patient(
    client: httpx.AsyncClient,
    care: Care,
    sessionmaker: async_sessionmaker[AsyncSession],
    notifications: list[dict[str, Any]],
) -> None:
    phone = await logged_in(client, PATIENT)
    async with sessionmaker() as session:
        laptop = await identity.create_session(session, care.patient.id, timedelta(hours=1))
        await session.commit()

    response = await client.post(REVOKE)
    await asyncio.sleep(0.2)
    async with sessionmaker() as session:
        active = await session.scalar(
            text("SELECT count(*) FROM identity.sessions WHERE revoked_at IS NULL")
        )

    assert response.status_code == 204
    assert active == 0
    ended = sorted(e["session_ended"] for e in notifications if "session_ended" in e)
    assert ended == sorted([tokens.token_hash(phone), tokens.token_hash(laptop)])


async def test_revoke_is_written_to_access_log_by_the_patient(
    client: httpx.AsyncClient, care: Care, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    await logged_in(client, PATIENT)

    await client.post(REVOKE)
    async with sessionmaker() as session:
        entries = (
            await session.execute(
                text(
                    "SELECT actor_id, patient_id, at FROM identity.access_log"
                    " WHERE action = 'consent.revoke'"
                )
            )
        ).all()

    assert len(entries) == 1
    assert (entries[0].actor_id, entries[0].patient_id) == (care.patient.id, care.patient.id)
    assert isinstance(entries[0].at, datetime)


async def test_old_cookie_after_revoke_is_rejected_on_patient_endpoints(
    client: httpx.AsyncClient, care: Care
) -> None:
    token = await logged_in(client, PATIENT)
    assert (await client.post(REVOKE)).status_code == 204
    client.cookies.set(COOKIE, token)

    me = await client.get("/api/v1/auth/me")
    answer = await client.post(
        "/api/v1/assessments",
        json={
            "patient_id": str(care.patient.id),
            "submission_id": str(uuid4()),
            "scale_id": "phq-9",
            "scale_version": 1,
            "completed_at": "2026-10-24T10:00:00+03:00",
            "answers": {},
        },
    )
    message = await client.post(
        f"/api/v1/patients/{care.patient.id}/messages",
        json={
            "expected_doctor_id": str(care.doctor.id),
            "client_message_id": str(uuid4()),
            "text": "Здравствуйте",
        },
    )
    again = await client.post(REVOKE)

    assert [me.status_code, answer.status_code, message.status_code] == [401, 401, 401]
    # Повторный отзыв невозможен: сессий больше нет.
    assert again.status_code == 401


async def test_login_after_revoke_is_closed_with_consent_revoked(
    client: httpx.AsyncClient, care: Care
) -> None:
    await logged_in(client, PATIENT)
    await client.post(REVOKE)

    right = await login(client, PATIENT)
    wrong = await login(client, PATIENT, "wrong password")

    assert (right.status_code, right.json()["code"]) == (403, "consent_revoked")
    assert wrong.status_code == 401
    assert wrong.json().get("code") is None


async def test_doctor_cannot_revoke_consent_for_patient(
    client: httpx.AsyncClient, care: Care, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    await logged_in(client, DOCTOR)

    response = await client.post(REVOKE)
    async with sessionmaker() as session:
        revoked = await session.scalar(
            text("SELECT count(*) FROM patients.consents WHERE revoked_at IS NOT NULL")
        )
        disabled = await session.scalar(
            text("SELECT count(*) FROM identity.users WHERE disabled_at IS NOT NULL")
        )

    assert response.status_code == 403
    assert (revoked, disabled) == (0, 0)


async def test_revoke_without_session_is_unauthorized(client: httpx.AsyncClient) -> None:
    response = await client.post(REVOKE)

    assert response.status_code == 401


async def test_doctor_sees_revocation_date_and_still_reads_patient_data(
    client: httpx.AsyncClient, care: Care
) -> None:
    await logged_in(client, DOCTOR)
    before = (await client.get("/api/v1/patients")).json()
    client.cookies.clear()
    await logged_in(client, PATIENT)
    await client.post(REVOKE)
    await logged_in(client, DOCTOR)

    listed = (await client.get("/api/v1/patients")).json()
    card = await client.get(f"/api/v1/patients/{care.patient.id}")
    history = await client.get(f"/api/v1/patients/{care.patient.id}/assessments")

    assert before[0]["consent_revoked_at"] is None
    assert listed[0]["consent_revoked_at"] is not None
    assert card.status_code == 200
    assert card.json()["consent_revoked_at"] == listed[0]["consent_revoked_at"]
    assert history.status_code == 200


async def test_revoke_closes_every_patient_socket_with_4401(live_server: str, care: Care) -> None:
    phone = await session_cookie(live_server, PATIENT)
    laptop = await session_cookie(live_server, PATIENT)

    async with (
        ws_connect(live_server, phone) as phone_ws,
        ws_connect(live_server, laptop) as laptop_ws,
    ):
        await receive(phone_ws)  # ready
        await receive(laptop_ws)  # ready
        async with httpx.AsyncClient(base_url=f"http://{live_server}") as http:
            revoked = await http.post(REVOKE, headers={"Cookie": phone})
        codes = [await closed_with(phone_ws), await closed_with(laptop_ws)]

    assert revoked.status_code == 204
    assert codes == [4401, 4401]
