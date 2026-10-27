"""Отзыв сессии, сроки presence/typing и уборка сокета после ошибки отправки."""

import asyncio
import json
from datetime import datetime
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.websockets import WebSocket
from websockets.asyncio.client import ClientConnection
from websockets.exceptions import ConnectionClosed

from clin_compass.core import realtime
from clin_compass.core.tokens import token_hash
from clin_compass.modules.identity.models import AuthSession
from clin_compass.modules.messaging.models import PresenceLease, TypingState
from tests.live import receive, session_cookie, ws_connect
from tests.messaging.conftest import MakeMessagingCare, login


async def receive_type(ws: ClientConnection, event_type: str) -> dict[str, Any]:
    while True:
        event = await receive(ws)
        if event["type"] == event_type:
            return event


async def closed_with(ws: ClientConnection) -> int | None:
    async def wait_for_close() -> int | None:
        while True:
            try:
                await ws.recv()
            except ConnectionClosed:
                return ws.close_code

    return await asyncio.wait_for(wait_for_close(), timeout=5)


async def receive_message_id(ws: ClientConnection, message_id: str) -> dict[str, Any]:
    while True:
        event = await receive(ws)
        data = event.get("data")
        if (
            event.get("type") == "message.created"
            and isinstance(data, dict)
            and data.get("message_id") == message_id
        ):
            return event


async def receive_typing_clear(
    ws: ClientConnection, user_id: str, wait_seconds: float = 5
) -> dict[str, Any]:
    seen: list[object] = []

    async def wait_for_clear() -> dict[str, Any]:
        while True:
            event = await receive(ws)
            data = event.get("data")
            if event.get("type") != "message.typing" or not isinstance(data, dict):
                continue
            seen.append(data)
            if data.get("user_id") == user_id and data.get("typing_until") is None:
                return event

    try:
        return await asyncio.wait_for(wait_for_clear(), timeout=wait_seconds)
    except TimeoutError:
        pytest.fail(f"no typing clear for user {user_id}; observed data: {seen!r}", pytrace=True)


async def wait_for_typing_cleanup(
    sessionmaker: async_sessionmaker[AsyncSession], *, user_id: Any, wait_seconds: float = 2
) -> None:
    async def poll() -> None:
        while True:
            async with sessionmaker() as session:
                lease = await session.scalar(
                    select(PresenceLease.connection_id).where(PresenceLease.user_id == user_id)
                )
                typing = await session.scalar(
                    select(TypingState.connection_id).where(TypingState.user_id == user_id)
                )
            if lease is None and typing is None:
                return
            await asyncio.sleep(0.02)

    try:
        await asyncio.wait_for(poll(), timeout=wait_seconds)
    except TimeoutError:
        async with sessionmaker() as session:
            lease = await session.scalar(
                select(PresenceLease.connection_id).where(PresenceLease.user_id == user_id)
            )
            typing = await session.scalar(
                select(TypingState.connection_id).where(TypingState.user_id == user_id)
            )
        pytest.fail(f"connection cleanup left lease={lease!r}, typing={typing!r}")


async def wait_for_presence(
    client: httpx.AsyncClient, url: str, *, online: bool, wait_seconds: float = 2
) -> dict[str, Any]:
    async def poll() -> dict[str, Any]:
        while True:
            response = await client.get(url)
            assert response.status_code == 200
            body = response.json()
            if body["online"] is online:
                return body
            await asyncio.sleep(0.02)

    return await asyncio.wait_for(poll(), timeout=wait_seconds)


def typing_command(patient_id: str, doctor_id: str, *, active: bool = True) -> str:
    return json.dumps(
        {
            "type": "message.typing",
            "data": {
                "patient_id": patient_id,
                "expected_doctor_id": doctor_id,
                "active": active,
            },
        }
    )


async def test_silent_session_revoke_cannot_keep_presence_or_typing(
    live_server: str,
    client: httpx.AsyncClient,
    make_messaging_care: MakeMessagingCare,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    care = await make_messaging_care("doctor@example.com", "patient@example.com")
    patient_cookie = await session_cookie(live_server, care.patient.email)
    doctor_cookie = await session_cookie(live_server, care.doctor.email)
    patient_session_key = token_hash(patient_cookie.partition("=")[2])
    url = f"/api/v1/patients/{care.patient.id}/messaging-status"
    await login(client, care.doctor.email)

    async with (
        ws_connect(live_server, patient_cookie) as patient,
        ws_connect(live_server, doctor_cookie) as doctor,
    ):
        await receive(patient)
        await receive(doctor)
        await patient.send(typing_command(str(care.patient.id), str(care.doctor.id)))
        active = await receive_type(doctor, "message.typing")
        assert active["data"]["typing_until"] is not None
        assert active["data"]["user_id"] == str(care.patient.id)

        # Direct update simulates a lost revoke NOTIFY; the next command must recheck auth.
        async with sessionmaker() as session:
            await session.execute(
                update(AuthSession)
                .where(AuthSession.token_hash == patient_session_key)
                .values(revoked_at=func.now())
            )
            await session.commit()

        prohibited_message_id = "2f258219-7c95-48d6-a045-2fe0a2921d8d"
        async with sessionmaker() as session:
            await realtime.publish(
                session,
                [care.patient.id],
                "message.created",
                {
                    "patient_id": str(care.patient.id),
                    "doctor_id": str(care.doctor.id),
                    "message_id": prohibited_message_id,
                    "seq": 2,
                },
            )
            await session.commit()
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(receive_message_id(patient, prohibited_message_id), timeout=0.25)

        # Повторный active=true в пределах 2 с отбрасывается в памяти без обращения к БД
        # и ничего не продлевает. active=false выполняется всегда и заново проверяет сессию.
        await patient.send(typing_command(str(care.patient.id), str(care.doctor.id), active=False))
        assert await closed_with(patient) == 4401
        await wait_for_typing_cleanup(sessionmaker, user_id=care.patient.id)
        cleared = await receive_typing_clear(doctor, str(care.patient.id))
        assert cleared["data"]["typing_until"] is None

    status = await wait_for_presence(client, url, online=False)
    assert status["last_seen_at"] is not None
    async with sessionmaker() as session:
        assert (
            await session.scalar(
                select(PresenceLease.connection_id).where(
                    PresenceLease.user_id == care.patient.id,
                    PresenceLease.session_key == patient_session_key,
                )
            )
            is None
        )
        assert (
            await session.scalar(
                select(TypingState.connection_id).where(TypingState.user_id == care.patient.id)
            )
            is None
        )


async def test_typing_lease_expires_after_five_seconds(
    live_servers: tuple[str, str],
    client: httpx.AsyncClient,
    make_messaging_care: MakeMessagingCare,
) -> None:
    first, second = live_servers
    care = await make_messaging_care("doctor@example.com", "patient@example.com")
    patient_cookie = await session_cookie(first, care.patient.email)
    doctor_cookie = await session_cookie(second, care.doctor.email)
    await login(client, care.doctor.email)
    url = f"/api/v1/patients/{care.patient.id}/messaging-status"

    async with (
        ws_connect(first, patient_cookie) as patient,
        ws_connect(second, doctor_cookie) as doctor,
    ):
        await receive(patient)
        await receive(doctor)
        await patient.send(typing_command(str(care.patient.id), str(care.doctor.id)))
        event = await receive_type(doctor, "message.typing")
        initial = await client.get(url)
        assert initial.status_code == 200
        status_typing_until = initial.json()["typing_until"]
        event_typing_until = event["data"]["typing_until"]
        assert isinstance(status_typing_until, str)
        assert isinstance(event_typing_until, str)
        assert datetime.fromisoformat(
            status_typing_until.replace("Z", "+00:00")
        ) == datetime.fromisoformat(event_typing_until.replace("Z", "+00:00"))

        await asyncio.sleep(5.1)
        expired = await client.get(url)

    assert expired.status_code == 200
    assert expired.json()["typing_until"] is None


async def test_presence_lease_is_capped_by_session_expiry(
    live_server: str,
    client: httpx.AsyncClient,
    make_messaging_care: MakeMessagingCare,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    care = await make_messaging_care("doctor@example.com", "patient@example.com")
    patient_cookie = await session_cookie(live_server, care.patient.email)
    patient_session_key = token_hash(patient_cookie.partition("=")[2])
    await login(client, care.doctor.email)
    url = f"/api/v1/patients/{care.patient.id}/messaging-status"
    async with sessionmaker() as session:
        await session.execute(
            update(AuthSession)
            .where(AuthSession.token_hash == patient_session_key)
            .values(expires_at=func.now() + text("interval '3 seconds'"))
        )
        await session.commit()

    async with ws_connect(live_server, patient_cookie) as patient:
        await receive(patient)
        await wait_for_presence(client, url, online=True)
        async with sessionmaker() as session:
            expires_at = await session.scalar(
                select(AuthSession.expires_at).where(AuthSession.token_hash == patient_session_key)
            )
            lease_until = await session.scalar(
                select(PresenceLease.lease_until).where(
                    PresenceLease.session_key == patient_session_key
                )
            )
        assert expires_at is not None
        assert lease_until is not None
        assert lease_until <= expires_at
        assert await closed_with(patient) == 4401

    offline = await wait_for_presence(client, url, online=False)
    assert offline["last_seen_at"] is not None


async def test_hub_send_failure_removes_socket_and_presence_lease(
    live_server: str,
    app: Any,
    client: httpx.AsyncClient,
    make_messaging_care: MakeMessagingCare,
    sessionmaker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    care = await make_messaging_care("doctor@example.com", "patient@example.com")
    doctor_cookie = await session_cookie(live_server, care.doctor.email)
    await login(client, care.patient.email)
    url = f"/api/v1/patients/{care.patient.id}/messaging-status"
    message_id = "8e91e1a1-419b-4d65-9ceb-9a82f5db6138"
    original_send_json = WebSocket.send_json

    async def fail_target_message(socket: WebSocket, data: Any, mode: str = "text") -> None:
        event_data = data.get("data") if isinstance(data, dict) else None
        if (
            isinstance(event_data, dict)
            and data.get("type") == "message.created"
            and event_data.get("message_id") == message_id
        ):
            raise OSError("simulated WebSocket send failure")
        await original_send_json(socket, data, mode)

    monkeypatch.setattr(WebSocket, "send_json", fail_target_message)
    async with ws_connect(live_server, doctor_cookie) as doctor:
        await receive(doctor)
        await receive_type(doctor, "message.presence")
        async with sessionmaker() as session:
            await realtime.publish(
                session,
                [care.doctor.id],
                "message.created",
                {
                    "patient_id": str(care.patient.id),
                    "doctor_id": str(care.doctor.id),
                    "message_id": message_id,
                    "seq": 1,
                },
            )
            await session.commit()

        async def wait_for_hub_removal() -> None:
            while care.doctor.id in app.state.hub.connected_users():
                await asyncio.sleep(0.01)

        await asyncio.wait_for(wait_for_hub_removal(), timeout=2)
        status = await wait_for_presence(client, url, online=False)
        assert status["last_seen_at"] is not None

    async with sessionmaker() as session:
        assert (
            await session.scalar(
                select(PresenceLease.connection_id).where(PresenceLease.user_id == care.doctor.id)
            )
            is None
        )
