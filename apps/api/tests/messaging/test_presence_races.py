"""Реальные Postgres-гонки не должны рассинхронизировать presence и leases."""

import asyncio
import contextlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.core import realtime
from clin_compass.core.queue import asyncpg_dsn
from clin_compass.modules.identity import service as identity
from clin_compass.modules.identity.models import AuthSession
from clin_compass.modules.identity.schemas import ActiveSession, UserOut
from clin_compass.modules.messaging import presence
from clin_compass.modules.messaging.models import PresenceLease, UserPresence
from tests.messaging.conftest import MakeMessagingCare


class PresenceNotifications:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def on_notify(self, _connection: object, _pid: int, _channel: str, payload: str) -> None:
        event = json.loads(payload)
        if event.get("type") == "message.presence":
            self.events.append(event)


async def connect_notifications(
    postgres_url: str,
) -> tuple[asyncpg.Connection, PresenceNotifications]:
    connection = await asyncpg.connect(asyncpg_dsn(postgres_url))
    notifications = PresenceNotifications()
    await connection.add_listener(realtime.CHANNEL, notifications.on_notify)
    return connection, notifications


async def add_lease(
    sessionmaker: async_sessionmaker[AsyncSession],
    active: ActiveSession,
    *,
    connection_id: UUID,
) -> None:
    async with sessionmaker() as session:
        await presence.touch(
            session,
            active.user,
            connection_id=connection_id,
            session_key=active.key,
            session_expires_at=active.expires_at,
        )
        await session.commit()


async def create_active_session(
    sessionmaker: async_sessionmaker[AsyncSession], actor: UserOut
) -> ActiveSession:
    async with sessionmaker() as session:
        token = await identity.create_session(session, actor.id, timedelta(minutes=3))
        active = await identity.get_active_session(session, token)
        assert active is not None
        await session.commit()
    return active


async def wait_for_advisory_lock_wait(
    sessionmaker: async_sessionmaker[AsyncSession], backend_pid: int
) -> None:
    async def poll() -> None:
        while True:
            async with sessionmaker() as session:
                waiting = await session.scalar(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM pg_locks "
                        "WHERE pid = :pid AND locktype = 'advisory' AND NOT granted)"
                    ),
                    {"pid": backend_pid},
                )
            if waiting:
                return
            await asyncio.sleep(0.01)

    await asyncio.wait_for(poll(), timeout=2)


async def test_reconnect_serializes_with_disconnect_of_previous_lease(
    postgres_url: str,
    sessionmaker: async_sessionmaker[AsyncSession],
    make_messaging_care: MakeMessagingCare,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    care = await make_messaging_care(
        "reconnect-doctor@example.com", "reconnect-patient@example.com"
    )
    active = await create_active_session(sessionmaker, care.patient)
    old_connection = uuid4()
    new_connection = uuid4()
    await add_lease(
        sessionmaker,
        active,
        connection_id=old_connection,
    )
    pg_listener, notifications = await connect_notifications(postgres_url)
    reconnect_session = sessionmaker()
    disconnect_session = sessionmaker()
    reconnect_observed_online = asyncio.Event()
    continue_reconnect = asyncio.Event()
    disconnect_completed = asyncio.Event()
    original_is_online = presence.is_online

    async def pause_reconnect_after_read(session: AsyncSession, user_id: UUID) -> bool:
        online = await original_is_online(session, user_id)
        if session is reconnect_session:
            reconnect_observed_online.set()
            await continue_reconnect.wait()
        return online

    monkeypatch.setattr(presence, "is_online", pause_reconnect_after_read)

    async def reconnect() -> None:
        await presence.touch(
            reconnect_session,
            care.patient,
            connection_id=new_connection,
            session_key=active.key,
            session_expires_at=active.expires_at,
        )
        await reconnect_session.commit()

    async def disconnect_previous() -> None:
        await presence.disconnect(disconnect_session, care.patient, connection_id=old_connection)
        await disconnect_session.commit()
        disconnect_completed.set()

    reconnect_task = asyncio.create_task(reconnect())
    disconnect_task: asyncio.Task[None] | None = None
    try:
        await asyncio.wait_for(reconnect_observed_online.wait(), timeout=2)
        disconnect_task = asyncio.create_task(disconnect_previous())
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(disconnect_completed.wait(), timeout=0.25)
        continue_reconnect.set()
        await asyncio.gather(reconnect_task, disconnect_task)
        await asyncio.sleep(0.1)
        async with sessionmaker() as session:
            active_leases = await session.scalar(
                select(func.count(PresenceLease.connection_id)).where(
                    PresenceLease.user_id == care.patient.id,
                    PresenceLease.lease_until > func.now(),
                )
            )
        online_values = [event["data"]["online"] for event in notifications.events]
        assert active_leases == 1
        assert not online_values or online_values[-1] is True
    finally:
        continue_reconnect.set()
        if not reconnect_task.done():
            reconnect_task.cancel()
        if disconnect_task is not None and not disconnect_task.done():
            disconnect_task.cancel()
        tasks = [reconnect_task]
        if disconnect_task is not None:
            tasks.append(disconnect_task)
        await asyncio.gather(*tasks, return_exceptions=True)
        await reconnect_session.close()
        await disconnect_session.close()
        await pg_listener.close()


async def test_simultaneous_disconnects_publish_offline_for_last_lease(
    postgres_url: str,
    sessionmaker: async_sessionmaker[AsyncSession],
    make_messaging_care: MakeMessagingCare,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    care = await make_messaging_care(
        "disconnect-doctor@example.com", "disconnect-patient@example.com"
    )
    active = await create_active_session(sessionmaker, care.patient)
    first_connection = uuid4()
    second_connection = uuid4()
    await add_lease(
        sessionmaker,
        active,
        connection_id=first_connection,
    )
    await add_lease(
        sessionmaker,
        active,
        connection_id=second_connection,
    )
    pg_listener, notifications = await connect_notifications(postgres_url)
    first_session = sessionmaker()
    second_session = sessionmaker()
    closing_sessions = {first_session, second_session}
    both_checked_presence = asyncio.Event()
    check_count = 0
    original_is_online = presence.is_online

    async def hold_checks_until_both_disconnects(session: AsyncSession, user_id: UUID) -> bool:
        nonlocal check_count
        online = await original_is_online(session, user_id)
        if session in closing_sessions:
            check_count += 1
            if check_count == 2:
                both_checked_presence.set()
            try:
                await asyncio.wait_for(both_checked_presence.wait(), timeout=0.5)
            except TimeoutError:
                both_checked_presence.set()
        return online

    monkeypatch.setattr(presence, "is_online", hold_checks_until_both_disconnects)

    async def disconnect(session: AsyncSession, connection_id: UUID) -> None:
        await presence.disconnect(session, care.patient, connection_id=connection_id)
        await session.commit()

    try:
        await asyncio.gather(
            disconnect(first_session, first_connection),
            disconnect(second_session, second_connection),
        )
        await asyncio.sleep(0.1)
        async with sessionmaker() as session:
            active_leases = await session.scalar(
                select(func.count(PresenceLease.connection_id)).where(
                    PresenceLease.user_id == care.patient.id,
                    PresenceLease.lease_until > func.now(),
                )
            )
        offline_events = [
            event
            for event in notifications.events
            if event["data"]["user_id"] == str(care.patient.id) and event["data"]["online"] is False
        ]
        assert active_leases == 0
        assert len(offline_events) == 1
    finally:
        await first_session.close()
        await second_session.close()
        await pg_listener.close()


@pytest.mark.parametrize("invalidate_as", ["expired", "revoked"])
async def test_touch_rechecks_session_after_waiting_for_user_lock(
    invalidate_as: str,
    postgres_url: str,
    sessionmaker: async_sessionmaker[AsyncSession],
    make_messaging_care: MakeMessagingCare,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    care = await make_messaging_care(
        f"touch-lock-{invalidate_as}-doctor@example.com",
        f"touch-lock-{invalidate_as}-patient@example.com",
    )
    active = await create_active_session(sessionmaker, care.patient)
    connection_id = uuid4()
    pg_listener, notifications = await connect_notifications(postgres_url)
    lock_session = sessionmaker()
    touch_session = sessionmaker()
    await presence._lock_user(lock_session, care.patient.id)
    original_lock_user = presence._lock_user
    waiting_for_lock = asyncio.Event()

    async def report_lock_attempt(session: AsyncSession, user_id: UUID) -> None:
        if session is touch_session:
            waiting_for_lock.set()
        await original_lock_user(session, user_id)

    monkeypatch.setattr(presence, "_lock_user", report_lock_attempt)
    touch_task: asyncio.Task[None] | None = None

    async def touch_after_wait() -> None:
        await presence.touch(
            touch_session,
            care.patient,
            connection_id=connection_id,
            session_key=active.key,
            session_expires_at=active.expires_at,
        )
        await touch_session.commit()

    try:
        backend_pid = await touch_session.scalar(text("SELECT pg_backend_pid()"))
        assert backend_pid is not None
        touch_task = asyncio.create_task(touch_after_wait())
        await asyncio.wait_for(waiting_for_lock.wait(), timeout=2)
        await wait_for_advisory_lock_wait(sessionmaker, backend_pid)

        async with sessionmaker() as session:
            if invalidate_as == "expired":
                await session.execute(
                    update(AuthSession)
                    .where(AuthSession.token_hash == active.key)
                    .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
                )
            else:
                await session.execute(
                    update(AuthSession)
                    .where(AuthSession.token_hash == active.key)
                    .values(revoked_at=datetime.now(UTC))
                )
            assert await identity.get_active_session_by_key(session, active.key) is None
            await session.commit()

        await lock_session.commit()
        await asyncio.wait_for(touch_task, timeout=2)
        await asyncio.sleep(0.1)
        async with sessionmaker() as session:
            lease = await session.get(PresenceLease, connection_id)
            last_seen = await session.get(UserPresence, care.patient.id)
        assert lease is None
        assert last_seen is None
        assert notifications.events == []
    finally:
        if lock_session.in_transaction():
            await lock_session.rollback()
        if touch_task is not None and not touch_task.done():
            touch_task.cancel()
        if touch_task is not None:
            await asyncio.gather(touch_task, return_exceptions=True)
        await touch_session.close()
        await lock_session.close()
        await pg_listener.close()
