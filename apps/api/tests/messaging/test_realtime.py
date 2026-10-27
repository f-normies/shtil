"""Сквозное WebSocket-состояние чата между независимыми API-репликами."""

import asyncio
import json
from datetime import datetime

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from websockets.asyncio.client import ClientConnection
from websockets.exceptions import ConnectionClosed

from clin_compass.core import realtime
from clin_compass.modules.identity.schemas import Role
from clin_compass.modules.patients.models import Patient
from tests.conftest import MakeUser
from tests.live import receive, session_cookie, ws_connect
from tests.messaging.conftest import MakeMessagingCare, login


async def receive_type(ws: ClientConnection, event_type: str) -> dict[str, object]:
    while True:
        event = await receive(ws)
        if event["type"] == event_type:
            return event


async def receive_message_id(ws: ClientConnection, message_id: str) -> dict[str, object]:
    while True:
        event = await receive(ws)
        data = event.get("data")
        if (
            event.get("type") == "message.created"
            and isinstance(data, dict)
            and data.get("message_id") == message_id
        ):
            return event


async def wait_for_presence(
    client: httpx.AsyncClient, url: str, *, online: bool, wait_seconds: float = 2
) -> dict[str, object]:
    async def poll() -> dict[str, object]:
        while True:
            response = await client.get(url)
            assert response.status_code == 200
            body = response.json()
            if body["online"] is online:
                return body
            await asyncio.sleep(0.02)

    return await asyncio.wait_for(poll(), timeout=wait_seconds)


async def closed_with(ws: ClientConnection) -> int | None:
    with pytest.raises(ConnectionClosed):
        await asyncio.wait_for(ws.recv(), timeout=5)
    return ws.close_code


async def test_typing_reaches_the_current_pair_on_another_api_instance(
    live_servers: tuple[str, str], make_messaging_care: MakeMessagingCare
) -> None:
    first, second = live_servers
    care = await make_messaging_care("doctor@example.com", "patient@example.com")
    patient_cookie = await session_cookie(first, care.patient.email)
    doctor_cookie = await session_cookie(second, care.doctor.email)

    async with (
        ws_connect(first, patient_cookie) as patient_ws,
        ws_connect(second, doctor_cookie) as doctor_ws,
    ):
        assert await receive(patient_ws) == {"type": "ready", "data": {}}
        assert await receive(doctor_ws) == {"type": "ready", "data": {}}
        await patient_ws.send(
            json.dumps(
                {
                    "type": "message.typing",
                    "data": {
                        "patient_id": str(care.patient.id),
                        "expected_doctor_id": str(care.doctor.id),
                        "active": True,
                    },
                }
            )
        )

        event = await receive_type(doctor_ws, "message.typing")

    assert event["type"] == "message.typing"
    data = event["data"]
    assert data["patient_id"] == str(care.patient.id)
    assert data["doctor_id"] == str(care.doctor.id)
    assert data["user_id"] == str(care.patient.id)
    assert isinstance(data["typing_until"], str)


async def test_typing_is_aggregated_per_pair_and_clears_per_connection(
    live_servers: tuple[str, str], make_messaging_care: MakeMessagingCare
) -> None:
    first, second = live_servers
    care = await make_messaging_care("doctor@example.com", "patient@example.com")
    patient_cookie_one = await session_cookie(first, care.patient.email)
    patient_cookie_two = await session_cookie(second, care.patient.email)
    doctor_cookie = await session_cookie(first, care.doctor.email)
    command = json.dumps(
        {
            "type": "message.typing",
            "data": {
                "patient_id": str(care.patient.id),
                "expected_doctor_id": str(care.doctor.id),
                "active": True,
            },
        }
    )
    stop = json.dumps(
        {
            "type": "message.typing",
            "data": {
                "patient_id": str(care.patient.id),
                "expected_doctor_id": str(care.doctor.id),
                "active": False,
            },
        }
    )

    async with (
        ws_connect(first, patient_cookie_one) as patient_one,
        ws_connect(second, patient_cookie_two) as patient_two,
        ws_connect(first, doctor_cookie) as doctor,
    ):
        await receive(patient_one)
        await receive(patient_two)
        await receive(doctor)
        await patient_one.send(command)
        first_active = await receive_type(doctor, "message.typing")
        await patient_two.send(command)
        second_active = await receive_type(doctor, "message.typing")
        await patient_one.send(stop)
        after_one_stops = await receive_type(doctor, "message.typing")
        await patient_two.send(stop)
        after_both_stop = await receive_type(doctor, "message.typing")

    assert first_active["data"]["typing_until"] is not None
    assert second_active["data"]["typing_until"] is not None
    assert after_one_stops["data"]["typing_until"] is not None
    assert after_one_stops["data"]["typing_until"] == second_active["data"]["typing_until"]
    assert after_both_stop["data"]["typing_until"] is None


async def test_typing_active_is_rate_limited_per_connection_and_pair(
    live_servers: tuple[str, str], make_messaging_care: MakeMessagingCare
) -> None:
    first, second = live_servers
    care = await make_messaging_care("doctor@example.com", "patient@example.com")
    patient_cookie = await session_cookie(first, care.patient.email)
    doctor_cookie = await session_cookie(second, care.doctor.email)
    command = json.dumps(
        {
            "type": "message.typing",
            "data": {
                "patient_id": str(care.patient.id),
                "expected_doctor_id": str(care.doctor.id),
                "active": True,
            },
        }
    )

    async with (
        ws_connect(first, patient_cookie) as patient,
        ws_connect(second, doctor_cookie) as doctor,
    ):
        await receive(patient)
        await receive(doctor)
        await patient.send(command)
        event = await receive_type(doctor, "message.typing")
        await patient.send(command)
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(doctor.recv(), timeout=0.25)

    assert event["data"]["typing_until"] is not None


async def test_new_text_message_clears_typing_from_all_sender_connections(
    live_servers: tuple[str, str],
    make_messaging_care: MakeMessagingCare,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    first, second = live_servers
    care = await make_messaging_care("doctor@example.com", "patient@example.com")
    patient_cookie_one = await session_cookie(first, care.patient.email)
    patient_cookie_two = await session_cookie(second, care.patient.email)
    doctor_cookie = await session_cookie(first, care.doctor.email)
    command = json.dumps(
        {
            "type": "message.typing",
            "data": {
                "patient_id": str(care.patient.id),
                "expected_doctor_id": str(care.doctor.id),
                "active": True,
            },
        }
    )

    async with (
        ws_connect(first, patient_cookie_one) as patient_one,
        ws_connect(second, patient_cookie_two) as patient_two,
        ws_connect(first, doctor_cookie) as doctor,
        httpx.AsyncClient(base_url=f"http://{first}") as http,
    ):
        await receive(patient_one)
        await receive(patient_two)
        await receive(doctor)
        await patient_one.send(command)
        await receive_type(doctor, "message.typing")
        await patient_two.send(command)
        await receive_type(doctor, "message.typing")
        login_response = await http.post(
            "/api/v1/auth/login",
            json={"email": care.patient.email, "password": "correct horse battery staple"},
        )
        assert login_response.status_code == 200
        response = await http.post(
            f"/api/v1/patients/{care.patient.id}/messages",
            json={
                "expected_doctor_id": str(care.doctor.id),
                "client_message_id": "1e65ddc6-4f9f-4112-b736-dfd3f52edc99",
                "text": "Сообщение пациента",
            },
        )
        assert response.status_code == 201
        event = await receive_type(doctor, "message.typing")

    assert event["data"]["typing_until"] is None


async def test_presence_uses_live_leases_and_keeps_last_seen_after_disconnect(
    live_servers: tuple[str, str],
    client: httpx.AsyncClient,
    make_messaging_care: MakeMessagingCare,
) -> None:
    first, second = live_servers
    care = await make_messaging_care("doctor@example.com", "patient@example.com")
    first_cookie = await session_cookie(first, care.patient.email)
    second_cookie = await session_cookie(second, care.patient.email)
    await login(client, care.doctor.email)
    url = f"/api/v1/patients/{care.patient.id}/messaging-status"

    async with (
        ws_connect(first, first_cookie) as first_tab,
        ws_connect(second, second_cookie) as second_tab,
    ):
        await receive(first_tab)
        await receive(second_tab)
        online = await client.get(url)
        assert online.status_code == 200
        assert online.json()["online"] is True
        assert online.json()["last_seen_at"] is not None
        await first_tab.close()
        still_online = await client.get(url)
        assert still_online.json()["online"] is True
        await second_tab.close()

    offline = await wait_for_presence(client, url, online=False)
    offline_last_seen = offline["last_seen_at"]
    assert isinstance(offline_last_seen, str)
    previous_last_seen = datetime.fromisoformat(offline_last_seen.replace("Z", "+00:00"))

    reconnect_cookie = await session_cookie(first, care.patient.email)
    async with ws_connect(first, reconnect_cookie) as reconnect:
        await receive(reconnect)
        online_again = await wait_for_presence(client, url, online=True)
        online_last_seen = online_again["last_seen_at"]
        assert isinstance(online_last_seen, str)
        current_last_seen = datetime.fromisoformat(online_last_seen.replace("Z", "+00:00"))
        assert current_last_seen >= previous_last_seen

    offline_again = await wait_for_presence(client, url, online=False)
    assert offline_again["last_seen_at"] is not None


async def test_message_delivery_rechecks_session_and_current_pair_per_socket(
    live_servers: tuple[str, str],
    make_messaging_care: MakeMessagingCare,
    make_user: MakeUser,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    _, second = live_servers
    care = await make_messaging_care("doctor@example.com", "patient@example.com")
    old_doctor_cookie = await session_cookie(second, care.doctor.email)
    async with ws_connect(second, old_doctor_cookie) as old_doctor:
        await receive(old_doctor)
        # `ready` precedes the presence transaction; drain its legitimate event so
        # the assertion below observes only the specifically prohibited message.
        await receive_type(old_doctor, "message.presence")
        replacement = await make_user(email="replacement@example.com", role=Role.DOCTOR)
        async with sessionmaker() as session:
            patient = await session.get(Patient, care.patient.id)
            assert patient is not None
            patient.lead_doctor_id = replacement.id
            await session.commit()
        prohibited_message_id = "bafecf43-3b4d-47ba-9173-0adef2cf0ff0"
        async with sessionmaker() as session:
            await realtime.publish(
                session,
                [care.patient.id, care.doctor.id],
                "message.created",
                {
                    "patient_id": str(care.patient.id),
                    "doctor_id": str(care.doctor.id),
                    "message_id": prohibited_message_id,
                    "seq": 1,
                },
            )
            await session.commit()
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(
                receive_message_id(old_doctor, prohibited_message_id), timeout=0.25
            )


async def test_websocket_rejects_binary_and_oversized_frames(
    live_server: str, make_messaging_care: MakeMessagingCare
) -> None:
    care = await make_messaging_care("doctor@example.com", "patient@example.com")
    cookie = await session_cookie(live_server, care.patient.email)

    async with ws_connect(live_server, cookie) as binary_ws:
        await receive(binary_ws)
        await binary_ws.send(b"binary")
        assert await closed_with(binary_ws) == 1003

    async with ws_connect(live_server, cookie) as oversized_ws:
        await receive(oversized_ws)
        await oversized_ws.send("x" * 4097)
        assert await closed_with(oversized_ws) == 1009


async def test_admin_cannot_receive_messaging_events_or_use_typing(
    live_servers: tuple[str, str],
    make_messaging_care: MakeMessagingCare,
    make_user: MakeUser,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    first, second = live_servers
    care = await make_messaging_care("doctor@example.com", "patient@example.com")
    admin = await make_user(email="admin@example.com", role=Role.ADMIN)
    admin_cookie = await session_cookie(second, admin.email)
    patient_cookie = await session_cookie(first, care.patient.email)
    command = json.dumps(
        {
            "type": "message.typing",
            "data": {
                "patient_id": str(care.patient.id),
                "expected_doctor_id": str(care.doctor.id),
                "active": True,
            },
        }
    )

    async with (
        ws_connect(first, patient_cookie) as patient,
        ws_connect(second, admin_cookie) as admin_ws,
    ):
        await receive(patient)
        await receive(admin_ws)
        await admin_ws.send(command)
        error = await receive_type(admin_ws, "message.error")
        async with sessionmaker() as session:
            await realtime.publish(
                session,
                [admin.id],
                "message.created",
                {
                    "patient_id": str(care.patient.id),
                    "doctor_id": str(care.doctor.id),
                    "message_id": "bafecf43-3b4d-47ba-9173-0adef2cf0ff0",
                    "seq": 1,
                },
            )
            await session.commit()
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(admin_ws.recv(), timeout=0.25)

    assert error["data"] == {"code": "forbidden"}
