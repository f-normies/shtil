"""Голосовая отправка снимает ввод во вкладках только при первом приёме."""

import asyncio
import json
from typing import Any
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from websockets.asyncio.client import ClientConnection

from tests.live import receive, session_cookie, ws_connect
from tests.messaging.conftest import MakeMessagingCare, login


class MemoryStorage:
    """Хранилище для маршрута приёма, которому нужен только put."""

    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}

    def put(self, key: str, data: bytes, content_type: str) -> None:
        self.objects[key] = (data, content_type)


async def receive_type(ws: ClientConnection, event_type: str) -> dict[str, Any]:
    async def read_until_match() -> dict[str, Any]:
        while True:
            event = await receive(ws)
            if event.get("type") == event_type:
                return event

    return await asyncio.wait_for(read_until_match(), timeout=5)


async def receive_typing_clear(ws: ClientConnection, patient_id: str) -> dict[str, Any]:
    async def read_until_clear() -> dict[str, Any]:
        while True:
            event = await receive(ws)
            data = event.get("data")
            if (
                event.get("type") == "message.typing"
                and isinstance(data, dict)
                and data.get("user_id") == patient_id
                and data.get("typing_until") is None
            ):
                return event

    return await asyncio.wait_for(read_until_clear(), timeout=5)


async def test_voice_send_clears_all_tabs_but_replay_has_no_typing_side_effect(
    live_server: str,
    app: FastAPI,
    make_messaging_care: MakeMessagingCare,
) -> None:
    care = await make_messaging_care(
        "voice-typing-doctor@example.com", "voice-typing-patient@example.com"
    )
    storage = MemoryStorage()
    app.state.storage = storage
    first_cookie = await session_cookie(live_server, care.patient.email)
    second_cookie = await session_cookie(live_server, care.patient.email)
    doctor_cookie = await session_cookie(live_server, care.doctor.email)
    message_id = uuid4()
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
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
        ws_connect(live_server, first_cookie) as first_tab,
        ws_connect(live_server, second_cookie) as second_tab,
        ws_connect(live_server, doctor_cookie) as doctor,
    ):
        await receive(first_tab)
        await receive(second_tab)
        await receive(doctor)
        await first_tab.send(command)
        first_active = await receive_type(doctor, "message.typing")
        await second_tab.send(command)
        second_active = await receive_type(doctor, "message.typing")
        assert first_active["data"]["typing_until"] is not None
        assert second_active["data"]["typing_until"] is not None

        await login(client, care.patient.email)
        request_data = {
            "expected_doctor_id": str(care.doctor.id),
            "client_message_id": str(message_id),
        }
        files = {"file": ("voice.webm", b"\x1aE\xdf\xa3voice", "audio/webm")}
        accepted = await client.post(
            f"/api/v1/patients/{care.patient.id}/messages/voice",
            data=request_data,
            files=files,
        )
        assert accepted.status_code == 201
        clear = await receive_typing_clear(doctor, str(care.patient.id))
        assert clear["data"]["typing_until"] is None

        await asyncio.sleep(2.05)
        await first_tab.send(command)
        await receive_type(doctor, "message.typing")
        await second_tab.send(command)
        await receive_type(doctor, "message.typing")
        replay = await client.post(
            f"/api/v1/patients/{care.patient.id}/messages/voice",
            data=request_data,
            files=files,
        )
        assert replay.status_code == 200

        with pytest.raises(TimeoutError):
            await asyncio.wait_for(doctor.recv(), timeout=0.25)

    assert len(storage.objects) == 1
