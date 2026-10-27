"""Команды realtime повторно проверяют текущую пару перед записью состояния."""

import asyncio
import json
from typing import Any
from uuid import uuid4

import pytest
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from websockets.asyncio.client import ClientConnection
from websockets.exceptions import ConnectionClosed

from clin_compass.core import realtime
from clin_compass.modules.identity.schemas import Role
from clin_compass.modules.messaging import service as messaging
from clin_compass.modules.messaging.models import TypingState
from clin_compass.modules.patients.models import Patient
from tests.conftest import MakeUser
from tests.live import receive, session_cookie, ws_connect
from tests.messaging.conftest import MakeMessagingCare


async def receive_type(ws: ClientConnection, event_type: str) -> dict[str, Any]:
    async def read_until_match() -> dict[str, Any]:
        while True:
            event = await receive(ws)
            if event.get("type") == event_type:
                return event

    return await asyncio.wait_for(read_until_match(), timeout=5)


async def test_stale_typing_command_is_rejected_without_writing_state(
    live_servers: tuple[str, str],
    make_messaging_care: MakeMessagingCare,
    make_user: MakeUser,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    first, _ = live_servers
    care = await make_messaging_care("stale-doctor@example.com", "stale-patient@example.com")
    replacement = await make_user(email="new-doctor@example.com", role=Role.DOCTOR)
    patient_cookie = await session_cookie(first, care.patient.email)

    async with ws_connect(first, patient_cookie) as patient_ws:
        assert await receive(patient_ws) == {"type": "ready", "data": {}}
        async with sessionmaker() as session:
            current_patient = await session.get(Patient, care.patient.id)
            assert current_patient is not None
            current_patient.lead_doctor_id = replacement.id
            await session.commit()

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
        error = await receive_type(patient_ws, "message.error")

    assert error["data"] == {"code": "conversation_changed"}
    async with sessionmaker() as session:
        count = await session.scalar(
            select(func.count(TypingState.connection_id)).where(
                TypingState.user_id == care.patient.id
            )
        )
    assert count == 0


async def test_delivery_guard_failure_disconnects_socket(
    app: FastAPI,
    live_server: str,
    make_user: MakeUser,
    sessionmaker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # У врача без пациентов нет пар: событий присутствия до проверяемого события не будет.
    doctor = await make_user(email="guard-failure-doctor@example.com", role=Role.DOCTOR)
    cookie = await session_cookie(live_server, doctor.email)
    received: list[dict[str, Any]] = []

    async def failing_pair_check(*_args: Any, **_kwargs: Any) -> bool:
        raise SQLAlchemyError("pair check failed")

    monkeypatch.setattr(messaging, "can_deliver_event", failing_pair_check)

    async with ws_connect(live_server, cookie) as doctor_ws:
        assert await receive(doctor_ws) == {"type": "ready", "data": {}}
        async with sessionmaker() as session:
            await realtime.publish(
                session,
                [doctor.id],
                "message.created",
                {
                    "patient_id": str(uuid4()),
                    "doctor_id": str(doctor.id),
                    "message_id": str(uuid4()),
                    "seq": 1,
                },
            )
            await session.commit()

        with pytest.raises(ConnectionClosed):
            while True:
                received.append(await receive(doctor_ws))

    assert received == []
    assert doctor.id not in app.state.hub.connected_users()
