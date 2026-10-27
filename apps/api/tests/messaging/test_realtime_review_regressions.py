"""End-to-end regressions for realtime heartbeat and session revalidation."""

import asyncio
import contextlib
import json
from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg
import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from websockets.asyncio.client import ClientConnection

from clin_compass.core import realtime
from clin_compass.core.queue import asyncpg_dsn
from clin_compass.core.tokens import token_hash
from clin_compass.modules.identity import service as identity
from clin_compass.modules.identity.models import AuthSession
from clin_compass.modules.messaging.models import PresenceLease, TypingState, UserPresence
from clin_compass.modules.patients import service as patients
from clin_compass.modules.realtime import api as realtime_api
from tests.live import receive, session_cookie, ws_connect
from tests.messaging.conftest import MakeMessagingCare


def _typing_command(patient_id: str, doctor_id: str) -> str:
    return json.dumps(
        {
            "type": "message.typing",
            "data": {
                "patient_id": patient_id,
                "expected_doctor_id": doctor_id,
                "active": True,
            },
        }
    )


async def _receive_type(ws: ClientConnection, event_type: str) -> dict[str, Any]:
    while True:
        event = await receive(ws)
        if event.get("type") == event_type:
            return event


async def _presence_times(
    sessionmaker: async_sessionmaker[AsyncSession], user_id: UUID
) -> tuple[datetime, datetime] | None:
    async with sessionmaker() as session:
        last_seen_at = await session.scalar(
            select(UserPresence.last_seen_at).where(UserPresence.user_id == user_id)
        )
        lease_until = await session.scalar(
            select(PresenceLease.lease_until)
            .where(
                PresenceLease.user_id == user_id,
                PresenceLease.lease_until > func.clock_timestamp(),
            )
            .order_by(PresenceLease.lease_until.desc())
            .limit(1)
        )
    if last_seen_at is None or lease_until is None:
        return None
    return last_seen_at, lease_until


async def _wait_for_presence_times(
    sessionmaker: async_sessionmaker[AsyncSession], user_id: UUID
) -> tuple[datetime, datetime]:
    async def poll() -> tuple[datetime, datetime]:
        while True:
            values = await _presence_times(sessionmaker, user_id)
            if values is not None:
                return values
            await asyncio.sleep(0.01)

    return await asyncio.wait_for(poll(), timeout=2)


async def test_heartbeat_refreshes_presence_during_continuous_input(
    live_server: str,
    sessionmaker: async_sessionmaker[AsyncSession],
    make_messaging_care: MakeMessagingCare,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(realtime_api, "HEARTBEAT_SECONDS", 0.12)
    care = await make_messaging_care(
        "heartbeat-doctor@example.com", "heartbeat-patient@example.com"
    )
    patient_cookie = await session_cookie(live_server, care.patient.email)
    doctor_cookie = await session_cookie(live_server, care.doctor.email)

    async with (
        ws_connect(live_server, patient_cookie) as patient,
        ws_connect(live_server, doctor_cookie) as doctor,
    ):
        assert (await receive(patient))["type"] == "ready"
        assert (await receive(doctor))["type"] == "ready"
        baseline = await _wait_for_presence_times(sessionmaker, care.patient.id)

        stop = asyncio.Event()

        async def send_invalid_commands() -> None:
            command = json.dumps({"type": "unknown", "data": {}})
            while not stop.is_set():
                await patient.send(command)
                await asyncio.sleep(0.02)

        async def drain_command_errors() -> None:
            while not stop.is_set():
                event = await receive(patient)
                if event.get("type") == "message.presence":
                    data = event.get("data")
                    assert isinstance(data, dict)
                    assert data.get("patient_id") == str(care.patient.id)
                    assert data.get("doctor_id") == str(care.doctor.id)
                    assert data.get("user_id") in {
                        str(care.patient.id),
                        str(care.doctor.id),
                    }
                    assert isinstance(data.get("online"), bool)
                    continue
                assert event == {"type": "message.error", "data": {"code": "invalid_command"}}

        sender = asyncio.create_task(send_invalid_commands())
        receiver = asyncio.create_task(drain_command_errors())
        refreshed: set[tuple[datetime, datetime]] = set()
        deadline = asyncio.get_running_loop().time() + 0.72
        try:
            while asyncio.get_running_loop().time() < deadline:
                assert not sender.done(), "continuous input sender stopped unexpectedly"
                assert not receiver.done(), "continuous input consumer stopped unexpectedly"
                current = await _presence_times(sessionmaker, care.patient.id)
                if current is not None and current[0] > baseline[0]:
                    refreshed.add(current)
                await asyncio.sleep(0.02)
        finally:
            stop.set()
            sender.cancel()
            receiver.cancel()
            for task in (sender, receiver):
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        assert len(refreshed) >= 2
        latest = max(refreshed, key=lambda values: values[0])
        assert latest[1] > baseline[1]

        await patient.send(_typing_command(str(care.patient.id), str(care.doctor.id)))
        typing = await asyncio.wait_for(_receive_type(doctor, "message.typing"), timeout=2)
        assert typing["data"]["user_id"] == str(care.patient.id)
        assert typing["data"]["typing_until"] is not None


async def test_typing_rechecks_revoked_session_after_patient_lock_wait(
    postgres_url: str,
    live_server: str,
    sessionmaker: async_sessionmaker[AsyncSession],
    make_messaging_care: MakeMessagingCare,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    care = await make_messaging_care(
        "typing-lock-doctor@example.com", "typing-lock-patient@example.com"
    )
    patient_cookie = await session_cookie(live_server, care.patient.email)
    doctor_cookie = await session_cookie(live_server, care.doctor.email)
    session_key = token_hash(patient_cookie.partition("=")[2])

    async with (
        ws_connect(live_server, patient_cookie) as patient,
        ws_connect(live_server, doctor_cookie) as doctor,
    ):
        assert (await receive(patient))["type"] == "ready"
        assert (await receive(doctor))["type"] == "ready"

        notifications: list[dict[str, Any]] = []

        def collect_typing_notification(
            _connection: asyncpg.Connection, _pid: int, _channel: str, payload: str
        ) -> None:
            event = json.loads(payload)
            if event.get("type") == "message.typing" and event.get("data", {}).get(
                "user_id"
            ) == str(care.patient.id):
                notifications.append(event)

        listener = await asyncpg.connect(asyncpg_dsn(postgres_url))
        await listener.add_listener(realtime.CHANNEL, collect_typing_notification)
        lock_session = sessionmaker()
        try:
            assert (
                await patients.lead_doctor_of_for_update(lock_session, care.patient.id)
            ) == care.doctor.id

            original_pair_lock = patients.lead_doctor_of_for_update
            backend_pid: asyncio.Future[int] = asyncio.get_running_loop().create_future()

            async def observe_patient_lock_wait(
                session: AsyncSession, patient_id: UUID
            ) -> UUID | None:
                if patient_id == care.patient.id and not backend_pid.done():
                    pid = await session.scalar(text("SELECT pg_backend_pid()"))
                    assert pid is not None
                    backend_pid.set_result(int(pid))
                return await original_pair_lock(session, patient_id)

            monkeypatch.setattr(patients, "lead_doctor_of_for_update", observe_patient_lock_wait)
            original_handle_typing = realtime_api._handle_typing
            handler_finished = asyncio.Event()

            async def observe_handler_completion(*args: Any, **kwargs: Any) -> Any:
                try:
                    return await original_handle_typing(*args, **kwargs)
                finally:
                    handler_finished.set()

            monkeypatch.setattr(realtime_api, "_handle_typing", observe_handler_completion)
            await patient.send(_typing_command(str(care.patient.id), str(care.doctor.id)))
            waiting_pid = await asyncio.wait_for(backend_pid, timeout=2)

            async def wait_for_row_lock_wait() -> None:
                async def poll() -> bool:
                    while True:
                        async with sessionmaker() as session:
                            waiting = await session.scalar(
                                text(
                                    "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                                    "WHERE pid = :pid AND wait_event_type = 'Lock')"
                                ),
                                {"pid": waiting_pid},
                            )
                        if waiting:
                            return True
                        await asyncio.sleep(0.01)

                await asyncio.wait_for(poll(), timeout=2)

            await wait_for_row_lock_wait()
            async with sessionmaker() as session:
                await session.execute(
                    update(AuthSession)
                    .where(AuthSession.token_hash == session_key)
                    .values(revoked_at=func.clock_timestamp())
                )
                await session.commit()
                assert await identity.get_active_session_by_key(session, session_key) is None

            await lock_session.commit()
            await asyncio.wait_for(handler_finished.wait(), timeout=2)
            await asyncio.sleep(0.05)

            async with sessionmaker() as session:
                active_typing = await session.scalar(
                    select(TypingState.connection_id).where(
                        TypingState.patient_id == care.patient.id,
                        TypingState.doctor_id == care.doctor.id,
                        TypingState.user_id == care.patient.id,
                        TypingState.expires_at > func.clock_timestamp(),
                    )
                )
            assert active_typing is None
            assert notifications == []
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(_receive_type(doctor, "message.typing"), timeout=0.25)
        finally:
            if lock_session.in_transaction():
                await lock_session.rollback()
            await lock_session.close()
            await listener.close()
