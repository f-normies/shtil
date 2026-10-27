"""Отключённый пользователь и отзыв всех его сессий (спека #112, 3.2–3.4)."""

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import asyncpg
import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.core import tokens
from clin_compass.core.queue import asyncpg_dsn
from clin_compass.core.realtime import CHANNEL
from clin_compass.modules.identity import service as identity
from clin_compass.modules.identity.schemas import DisabledReason, Role
from tests.conftest import MakeUser
from tests.throttling import limited_api

PASSWORD = "correct horse battery staple"
WRONG = "wrong password"
PATIENT = "patient@example.com"
IP = "203.0.113.1"
HOUR = timedelta(hours=1)


class Notifications:
    """NOTIFY канала событий, которые Postgres выдал после коммита."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def on_notify(self, _connection: object, _pid: int, _channel: str, payload: str) -> None:
        self.events.append(json.loads(payload))

    async def settle(self) -> list[dict[str, Any]]:
        await asyncio.sleep(0.2)
        return self.events


@pytest.fixture
async def notifications(postgres_url: str) -> AsyncIterator[Notifications]:
    collected = Notifications()
    connection = await asyncpg.connect(asyncpg_dsn(postgres_url))
    await connection.add_listener(CHANNEL, collected.on_notify)
    yield collected
    await connection.close()


async def login(client: httpx.AsyncClient, email: str, password: str) -> httpx.Response:
    return await client.post("/api/v1/auth/login", json={"email": email, "password": password})


async def disable(maker: async_sessionmaker[AsyncSession], user_id: Any) -> None:
    async with maker() as session:
        await identity.disable_user(session, user_id, DisabledReason.CONSENT_REVOKED)
        await session.commit()


async def test_revoke_user_sessions_ends_every_active_session_of_that_user_only(
    make_user: MakeUser,
    sessionmaker: async_sessionmaker[AsyncSession],
    notifications: Notifications,
) -> None:
    patient = await make_user(email=PATIENT, role=Role.PATIENT)
    other = await make_user(email="other@example.com", role=Role.PATIENT)
    async with sessionmaker() as session:
        phone = await identity.create_session(session, patient.id, HOUR)
        laptop = await identity.create_session(session, patient.id, HOUR)
        foreign = await identity.create_session(session, other.id, HOUR)
        await session.commit()

        await identity.revoke_user_sessions(session, patient.id)
        await session.commit()

        resolved = [await identity.resolve_session(session, t) for t in (phone, laptop, foreign)]
    events = await notifications.settle()

    assert resolved == [None, None, other]
    assert sorted(e["session_ended"] for e in events) == sorted(
        [tokens.token_hash(phone), tokens.token_hash(laptop)]
    )


async def test_revoke_user_sessions_keeps_earlier_revocation_and_does_not_announce_it_again(
    make_user: MakeUser,
    sessionmaker: async_sessionmaker[AsyncSession],
    notifications: Notifications,
) -> None:
    patient = await make_user(email=PATIENT, role=Role.PATIENT)
    async with sessionmaker() as session:
        token = await identity.create_session(session, patient.id, HOUR)
        await identity.revoke_session(session, token)
        await session.commit()
        first = await session.scalar(text("SELECT revoked_at FROM identity.sessions"))
    # Событие первого отзыва уже ушло: дальше считаем только новые.
    await notifications.settle()
    notifications.events.clear()

    async with sessionmaker() as session:
        await identity.revoke_user_sessions(session, patient.id)
        await session.commit()
        second = await session.scalar(text("SELECT revoked_at FROM identity.sessions"))
    events = await notifications.settle()

    assert second == first
    assert events == []


async def test_disabled_user_is_not_resolved_even_with_a_live_session(
    make_user: MakeUser, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    patient = await make_user(email=PATIENT, role=Role.PATIENT)
    async with sessionmaker() as session:
        token = await identity.create_session(session, patient.id, HOUR)
        await session.commit()
    await disable(sessionmaker, patient.id)

    async with sessionmaker() as session:
        by_token = await identity.get_active_session(session, token)
        by_key = await identity.get_active_session_by_key(session, tokens.token_hash(token))
        revoked_at = await session.scalar(text("SELECT revoked_at FROM identity.sessions"))

    assert (by_token, by_key) == (None, None)
    # Строка сессии жива: пускать перестала именно отметка об отключении.
    assert revoked_at is None


async def test_disable_user_records_reason_and_keeps_first_time(
    make_user: MakeUser, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    patient = await make_user(email=PATIENT, role=Role.PATIENT)
    await disable(sessionmaker, patient.id)
    query = text("SELECT disabled_at, disabled_reason FROM identity.users")
    async with sessionmaker() as session:
        first = (await session.execute(query)).one()
    await disable(sessionmaker, patient.id)
    async with sessionmaker() as session:
        second = (await session.execute(query)).one()

    assert first.disabled_at is not None
    assert first.disabled_reason == "consent_revoked"
    assert second == first


async def test_disabled_user_with_right_password_gets_403_with_reason(
    client: httpx.AsyncClient, make_user: MakeUser, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    patient = await make_user(email=PATIENT, role=Role.PATIENT)
    await disable(sessionmaker, patient.id)

    response = await login(client, PATIENT, PASSWORD)
    async with sessionmaker() as session:
        sessions = await session.scalar(text("SELECT count(*) FROM identity.sessions"))

    assert response.status_code == 403
    assert response.headers["content-type"] == "application/problem+json"
    assert (response.json()["status"], response.json()["code"]) == (403, "consent_revoked")
    assert "compass_session" not in response.cookies
    assert sessions == 0


async def test_disabled_user_with_wrong_password_gets_plain_401(
    client: httpx.AsyncClient, make_user: MakeUser, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    patient = await make_user(email=PATIENT, role=Role.PATIENT)
    await disable(sessionmaker, patient.id)

    response = await login(client, PATIENT, WRONG)

    # Тот же ответ, что у любого неверного пароля: отключение не раскрывается.
    assert response.status_code == 401
    assert response.json().get("code") is None


async def test_403_of_disabled_user_does_not_reset_failed_logins(
    postgres_url: str, make_user: MakeUser, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    patient = await make_user(email=PATIENT, role=Role.PATIENT)
    await disable(sessionmaker, patient.id)
    async with limited_api(postgres_url, login_failures_per_pair=3) as client_from:
        client = client_from(IP)
        failures = [(await login(client, PATIENT, WRONG)).status_code for _ in range(2)]
        right = await login(client, PATIENT, PASSWORD)
        third = await login(client, PATIENT, WRONG)
        blocked = await login(client, PATIENT, PASSWORD)

    # Успешный вход стёр бы две неудачи пары, и третья не исчерпала бы лимит.
    assert failures == [401, 401]
    assert right.status_code == 403
    assert third.status_code == 401
    assert blocked.status_code == 429


async def test_disabled_at_and_reason_are_set_only_together(
    make_user: MakeUser, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    # Без причины вход отвечал бы 500 вместо 403: база не даёт записать время без причины.
    patient = await make_user(email=PATIENT, role=Role.PATIENT)
    async with sessionmaker() as session:
        with pytest.raises(IntegrityError):
            await session.execute(
                text("UPDATE identity.users SET disabled_at = now() WHERE id = :id"),
                {"id": patient.id},
            )
