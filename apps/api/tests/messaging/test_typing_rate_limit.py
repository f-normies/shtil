"""Снятие индикатора не сбрасывает двухсекундный лимит активного ввода."""

import asyncio
import json
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from websockets.asyncio.client import ClientConnection

from clin_compass.modules.messaging import service as messaging
from clin_compass.modules.messaging.models import TypingState
from clin_compass.modules.realtime import api as realtime_api
from tests.live import receive, session_cookie, ws_connect
from tests.messaging.conftest import MakeMessagingCare, login


async def receive_type(ws: ClientConnection, event_type: str) -> dict[str, Any]:
    async def read_until_match() -> dict[str, Any]:
        while True:
            event = await receive(ws)
            if event.get("type") == event_type:
                return event

    return await asyncio.wait_for(read_until_match(), timeout=5)


def typing_command(patient_id: str, doctor_id: str, *, active: bool) -> str:
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


async def assert_no_active_typing_state(
    sessionmaker: async_sessionmaker[AsyncSession], user_id: UUID
) -> int:
    async with sessionmaker() as session:
        count = await session.scalar(
            select(func.count(TypingState.connection_id)).where(
                TypingState.user_id == user_id,
                TypingState.expires_at > func.now(),
            )
        )
    return int(count or 0)


async def rejected_retry_event(ws: ClientConnection) -> dict[str, Any] | None:
    try:
        return await asyncio.wait_for(receive_type(ws, "message.typing"), timeout=0.25)
    except TimeoutError:
        return None


async def test_active_false_does_not_reset_connection_pair_rate_limit(
    live_server: str,
    make_messaging_care: MakeMessagingCare,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    care = await make_messaging_care(
        "typing-stop-doctor@example.com", "typing-stop-patient@example.com"
    )
    patient_cookie = await session_cookie(live_server, care.patient.email)
    doctor_cookie = await session_cookie(live_server, care.doctor.email)
    active = typing_command(str(care.patient.id), str(care.doctor.id), active=True)
    stopped = typing_command(str(care.patient.id), str(care.doctor.id), active=False)

    async with (
        ws_connect(live_server, patient_cookie) as patient,
        ws_connect(live_server, doctor_cookie) as doctor,
    ):
        await receive(patient)
        await receive(doctor)
        await patient.send(active)
        first = await receive_type(doctor, "message.typing")
        assert first["data"]["typing_until"] is not None

        await patient.send(stopped)
        clear = await receive_type(doctor, "message.typing")
        assert clear["data"]["typing_until"] is None

        await patient.send(active)
        retry = await rejected_retry_event(doctor)
        assert await assert_no_active_typing_state(sessionmaker, care.patient.id) == 0
        assert retry is None


async def test_accepted_message_does_not_reset_connection_pair_rate_limit(
    live_server: str,
    make_messaging_care: MakeMessagingCare,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    care = await make_messaging_care(
        "typing-send-doctor@example.com", "typing-send-patient@example.com"
    )
    patient_cookie = await session_cookie(live_server, care.patient.email)
    doctor_cookie = await session_cookie(live_server, care.doctor.email)
    active = typing_command(str(care.patient.id), str(care.doctor.id), active=True)

    async with httpx.AsyncClient(base_url=f"http://{live_server}") as client:
        await login(client, care.patient.email)
        async with (
            ws_connect(live_server, patient_cookie) as patient,
            ws_connect(live_server, doctor_cookie) as doctor,
        ):
            await receive(patient)
            await receive(doctor)
            await patient.send(active)
            first = await receive_type(doctor, "message.typing")
            assert first["data"]["typing_until"] is not None

            sent = await client.post(
                f"/api/v1/patients/{care.patient.id}/messages",
                json={
                    "expected_doctor_id": str(care.doctor.id),
                    "client_message_id": str(uuid4()),
                    "text": "Новое сообщение снимает индикатор.",
                },
            )
            assert sent.status_code == 201
            clear = await receive_type(doctor, "message.typing")
            assert clear["data"]["user_id"] == str(care.patient.id)
            assert clear["data"]["typing_until"] is None

            await patient.send(active)
            retry = await rejected_retry_event(doctor)
            assert await assert_no_active_typing_state(sessionmaker, care.patient.id) == 0
            assert retry is None


async def _drain_until_command_error(ws: ClientConnection) -> None:
    # Кадры одного сокета обрабатываются по очереди: ответ на неизвестную команду
    # приходит, когда все кадры перед ней уже обработаны.
    await ws.send(json.dumps({"type": "unknown", "data": {}}))
    error = await receive_type(ws, "message.error")
    assert error["data"] == {"code": "invalid_command"}


async def test_throttled_typing_frames_do_not_reach_session_or_service(
    live_server: str,
    make_messaging_care: MakeMessagingCare,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    care = await make_messaging_care(
        "typing-flood-doctor@example.com", "typing-flood-patient@example.com"
    )
    patient_cookie = await session_cookie(live_server, care.patient.email)
    active = typing_command(str(care.patient.id), str(care.doctor.id), active=True)
    stopped = typing_command(str(care.patient.id), str(care.doctor.id), active=False)
    service_calls: list[bool] = []
    session_lookups: list[str] = []
    original_update = messaging.update_typing
    original_lookup = realtime_api._active_session

    async def count_update(*args: Any, **kwargs: Any) -> None:
        service_calls.append(kwargs["active"])
        await original_update(*args, **kwargs)

    async def count_lookup(*args: Any, **kwargs: Any) -> Any:
        session_lookups.append("lookup")
        return await original_lookup(*args, **kwargs)

    monkeypatch.setattr(messaging, "update_typing", count_update)
    monkeypatch.setattr(realtime_api, "_active_session", count_lookup)

    async with ws_connect(live_server, patient_cookie) as patient:
        assert (await receive(patient))["type"] == "ready"
        session_lookups.clear()
        for _ in range(10):
            await patient.send(active)
        await _drain_until_command_error(patient)

        assert service_calls == [True]
        assert len(session_lookups) == 1

        await patient.send(stopped)
        await _drain_until_command_error(patient)

    assert service_calls == [True, False]
