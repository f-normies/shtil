"""Realtime: событие из транзакции доходит по WebSocket только адресату и только после коммита.

Путь алерта из спеки (4.5): запись → NOTIFY при коммите → WebSocket врача.
Выход и истечение сессии закрывают её сокеты кодом 4401 (#14).
Тесты идут через настоящий uvicorn и настоящий WebSocket-клиент.
"""

import asyncio
import json
from uuid import UUID

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from websockets.asyncio.client import ClientConnection
from websockets.exceptions import ConnectionClosed, InvalidStatus

from clin_compass.core import realtime
from clin_compass.modules.identity import service as identity
from clin_compass.modules.identity.schemas import ActiveSession
from tests.conftest import MakeUser
from tests.live import receive, session_cookie, ws_connect


async def closed_with(ws: ClientConnection) -> int | None:
    """Ждёт закрытия сокета сервером и возвращает код закрытия."""
    with pytest.raises(ConnectionClosed):
        await asyncio.wait_for(ws.recv(), timeout=5)
    return ws.close_code


async def logout(host: str, cookie: str) -> None:
    async with httpx.AsyncClient(base_url=f"http://{host}") as client:
        response = await client.post("/api/v1/auth/logout", headers={"Cookie": cookie})
        assert response.status_code == 204


async def publish(
    maker: async_sessionmaker[AsyncSession], to: UUID, event: str, *, commit: bool = True
) -> None:
    async with maker() as session:
        await realtime.publish(session, [to], event, {"n": event})
        if commit:
            await session.commit()
        else:
            await session.rollback()


async def test_user_receives_event_published_for_them(
    live_server: str, make_user: MakeUser, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    doctor = await make_user(email="doctor@example.com")
    cookie = await session_cookie(live_server, "doctor@example.com")

    async with ws_connect(live_server, cookie) as ws:
        assert await receive(ws) == {"type": "ready", "data": {}}
        await publish(sessionmaker, doctor.id, "alert.created")
        message = await receive(ws)

    assert message == {"type": "alert.created", "data": {"n": "alert.created"}}


async def test_events_for_other_users_and_rolled_back_events_are_not_delivered(
    live_server: str, make_user: MakeUser, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    doctor = await make_user(email="doctor@example.com")
    other = await make_user(email="other@example.com")
    cookie = await session_cookie(live_server, "doctor@example.com")

    async with ws_connect(live_server, cookie) as ws:
        await receive(ws)  # ready
        await publish(sessionmaker, other.id, "for.other")
        await publish(sessionmaker, doctor.id, "rolled.back", commit=False)
        await publish(sessionmaker, doctor.id, "for.doctor")
        message = await receive(ws)

    # Первое пришедшее событие — последнее опубликованное: два предыдущих отсеяны.
    assert message["type"] == "for.doctor"


async def test_delivery_resumes_after_listener_connection_is_killed(
    live_server: str, make_user: MakeUser, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    doctor = await make_user(email="doctor@example.com")
    cookie = await session_cookie(live_server, "doctor@example.com")

    async with ws_connect(live_server, cookie) as ws:
        await receive(ws)  # ready
        async with sessionmaker() as session:
            killed = await session.scalar(
                text(
                    "SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity "
                    "WHERE query ILIKE 'LISTEN%compass_events%'"
                )
            )
        # Пока слушатель переподключается, события теряются: клиент дочитывает
        # пропущенное по REST. Поэтому публикуем, пока одно не дойдёт.
        message = None
        for _ in range(50):
            await publish(sessionmaker, doctor.id, "after.reconnect")
            try:
                message = json.loads(await asyncio.wait_for(ws.recv(), timeout=0.2))
                break
            except TimeoutError:
                continue

    assert killed == 1
    assert message == {"type": "after.reconnect", "data": {"n": "after.reconnect"}}


async def test_websocket_without_session_is_rejected(live_server: str) -> None:
    with pytest.raises(InvalidStatus) as rejected:
        async with ws_connect(live_server, cookie=None):
            pass

    assert rejected.value.response.status_code == 403


async def test_websocket_from_foreign_origin_is_rejected(
    live_server: str, make_user: MakeUser
) -> None:
    await make_user(email="doctor@example.com")
    cookie = await session_cookie(live_server, "doctor@example.com")

    with pytest.raises(InvalidStatus) as rejected:
        async with ws_connect(live_server, cookie, origin="https://evil.example.com"):
            pass

    assert rejected.value.response.status_code == 403


async def test_publish_rejects_oversized_payload(
    sessionmaker: async_sessionmaker[AsyncSession], make_user: MakeUser
) -> None:
    user = await make_user()
    async with sessionmaker() as session:
        with pytest.raises(ValueError, match="too large"):
            await realtime.publish(session, [user.id], "big", {"blob": "x" * 9000})


async def test_logout_closes_only_sockets_of_that_session(
    live_server: str, make_user: MakeUser, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    doctor = await make_user(email="doctor@example.com")
    laptop = await session_cookie(live_server, "doctor@example.com")
    phone = await session_cookie(live_server, "doctor@example.com")

    async with (
        ws_connect(live_server, laptop) as laptop_ws,
        ws_connect(live_server, phone) as phone_ws,
    ):
        await receive(laptop_ws)  # ready
        await receive(phone_ws)  # ready
        await logout(live_server, laptop)
        code = await closed_with(laptop_ws)
        await publish(sessionmaker, doctor.id, "after.logout")
        message = await receive(phone_ws)

    assert code == 4401
    assert message == {"type": "after.logout", "data": {"n": "after.logout"}}


async def test_server_closes_socket_when_session_expires(
    live_server: str, make_user: MakeUser, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    await make_user(email="doctor@example.com")
    cookie = await session_cookie(live_server, "doctor@example.com")
    async with sessionmaker() as session:
        await session.execute(
            text("UPDATE identity.sessions SET expires_at = now() + interval '2 seconds'")
        )
        await session.commit()
    started = asyncio.get_running_loop().time()

    async with ws_connect(live_server, cookie) as ws:
        assert await receive(ws) == {"type": "ready", "data": {}}
        code = await closed_with(ws)
    elapsed = asyncio.get_running_loop().time() - started

    assert code == 4401
    # Закрывается по сроку сессии, а не сразу после подключения.
    assert elapsed > 1.5


async def test_logout_between_session_check_and_registration_closes_socket(
    live_server: str,
    make_user: MakeUser,
    sessionmaker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await make_user(email="doctor@example.com")
    cookie = await session_cookie(live_server, "doctor@example.com")
    original = identity.get_active_session
    checks = 0

    async def revoked_right_after_first_check(
        session: AsyncSession, token: str
    ) -> ActiveSession | None:
        nonlocal checks
        checks += 1
        active = await original(session, token)
        if checks == 1:
            # Выход завершился до регистрации сокета в хабе: его NOTIFY закрывать было нечего.
            async with sessionmaker() as other:
                await other.execute(text("UPDATE identity.sessions SET revoked_at = now()"))
                await other.commit()
        return active

    monkeypatch.setattr(identity, "get_active_session", revoked_right_after_first_check)

    async with ws_connect(live_server, cookie) as ws:
        code = await closed_with(ws)

    assert code == 4401
