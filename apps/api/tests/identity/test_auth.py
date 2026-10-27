"""Вход, сессии и выход (спека фундамента, 4.6): серверная сессия в httpOnly-cookie."""

from datetime import timedelta

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.modules.identity import service as identity
from clin_compass.modules.identity.schemas import Role
from tests.conftest import MakeUser

PASSWORD = "correct horse battery staple"
COOKIE = "compass_session"


async def login(client: httpx.AsyncClient, email: str, password: str) -> httpx.Response:
    return await client.post("/api/v1/auth/login", json={"email": email, "password": password})


async def test_login_sets_httponly_session_cookie_and_returns_user(
    client: httpx.AsyncClient, make_user: MakeUser
) -> None:
    await make_user(email="doctor@example.com", password=PASSWORD)

    response = await login(client, "doctor@example.com", PASSWORD)

    assert response.status_code == 200
    body = response.json()
    assert body["email"] == "doctor@example.com"
    assert body["role"] == "doctor"
    assert "password_hash" not in body
    set_cookie = response.headers["set-cookie"]
    assert set_cookie.startswith(f"{COOKIE}=")
    assert "HttpOnly" in set_cookie
    assert "SameSite=lax" in set_cookie


async def test_login_email_is_case_insensitive(
    client: httpx.AsyncClient, make_user: MakeUser
) -> None:
    await make_user(email="doctor@example.com", password=PASSWORD)

    response = await login(client, "Doctor@Example.COM", PASSWORD)

    assert response.status_code == 200


async def test_login_with_wrong_password_is_rejected_as_problem_json(
    client: httpx.AsyncClient, make_user: MakeUser
) -> None:
    await make_user(email="doctor@example.com", password=PASSWORD)

    response = await login(client, "doctor@example.com", "wrong password")

    assert response.status_code == 401
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["status"] == 401
    assert COOKIE not in response.cookies


async def test_login_with_unknown_email_is_rejected(client: httpx.AsyncClient) -> None:
    response = await login(client, "nobody@example.com", PASSWORD)

    assert response.status_code == 401


async def test_me_returns_current_user_for_valid_session(
    client: httpx.AsyncClient, make_user: MakeUser
) -> None:
    user = await make_user(email="patient@example.com", password=PASSWORD, role=Role.PATIENT)
    await login(client, "patient@example.com", PASSWORD)

    response = await client.get("/api/v1/auth/me")

    assert response.status_code == 200
    assert response.json()["id"] == str(user.id)
    assert response.json()["role"] == "patient"


async def test_me_without_session_is_unauthorized(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/auth/me")

    assert response.status_code == 401


async def test_me_with_forged_token_is_unauthorized(client: httpx.AsyncClient) -> None:
    client.cookies.set(COOKIE, "forged-token")

    response = await client.get("/api/v1/auth/me")

    assert response.status_code == 401


async def test_logout_revokes_session_server_side(
    client: httpx.AsyncClient, make_user: MakeUser
) -> None:
    await make_user(email="doctor@example.com", password=PASSWORD)
    await login(client, "doctor@example.com", PASSWORD)
    token = client.cookies[COOKIE]

    logout = await client.post("/api/v1/auth/logout")
    # Повторно подставляем старый токен: сессия должна быть отозвана на сервере,
    # а не просто стёрта из cookie браузера.
    client.cookies.set(COOKIE, token)
    me = await client.get("/api/v1/auth/me")

    assert logout.status_code == 204
    assert me.status_code == 401


async def test_repeated_revoke_keeps_original_revocation_time(
    make_user: MakeUser, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    user = await make_user()
    async with sessionmaker() as session:
        token = await identity.create_session(session, user.id, ttl=timedelta(hours=1))
        await identity.revoke_session(session, token)
        await session.commit()
        first = await session.scalar(text("SELECT revoked_at FROM identity.sessions"))
        await identity.revoke_session(session, token)
        await session.commit()
        second = await session.scalar(text("SELECT revoked_at FROM identity.sessions"))

    assert first is not None
    assert second == first


async def test_expired_session_is_unauthorized(
    client: httpx.AsyncClient,
    make_user: MakeUser,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    user = await make_user()
    async with sessionmaker() as session:
        token = await identity.create_session(session, user.id, ttl=timedelta(seconds=-1))
        await session.commit()
    client.cookies.set(COOKIE, token)

    response = await client.get("/api/v1/auth/me")

    assert response.status_code == 401


async def test_user_is_found_by_email_ignoring_case_and_spaces(
    make_user: MakeUser, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    user = await make_user(email="doctor@example.com")

    async with sessionmaker() as session:
        found = await identity.get_user_by_email(session, "  Doctor@Example.com ")
        missing = await identity.get_user_by_email(session, "other@example.com")

    assert found == user
    assert missing is None
