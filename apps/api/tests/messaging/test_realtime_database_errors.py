"""Ошибки БД в WebSocket не выводят в логи SQL и параметры: там ID пользователей и хеш сессии."""

import asyncio
import contextlib
import json
import logging
from collections.abc import Iterator, MutableMapping
from typing import Any

import pytest
import structlog
from sqlalchemy.exc import DBAPIError
from websockets.asyncio.client import ClientConnection
from websockets.exceptions import ConnectionClosed, InvalidStatus

from clin_compass.modules.identity import service as identity
from clin_compass.modules.messaging import presence
from clin_compass.modules.realtime import api as realtime_api
from tests.live import receive, session_cookie, ws_connect
from tests.messaging.conftest import MakeMessagingCare

SENTINEL = "sentinel-3f9c2a"
LOGGERS = ("", "uvicorn", "uvicorn.error", "asyncio")


def _database_error() -> DBAPIError:
    return DBAPIError(
        "SELECT * FROM messaging.typing_states WHERE session_key = %(key)s",
        {"key": SENTINEL},
        Exception(f"driver failure {SENTINEL}"),
    )


async def _raise_database_error(*_args: Any, **_kwargs: Any) -> Any:
    raise _database_error()


class _Records(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        # Formatter добавляет traceback и текст исключения: именно там была утечка.
        self.lines.append(logging.Formatter("%(name)s %(message)s").format(record))


@contextlib.contextmanager
def _captured_logs() -> Iterator[tuple[_Records, list[MutableMapping[str, Any]]]]:
    handler = _Records()
    loggers = [logging.getLogger(name) for name in LOGGERS]
    for logger in loggers:
        logger.addHandler(handler)
    try:
        with structlog.testing.capture_logs() as events:
            yield handler, events
    finally:
        for logger in loggers:
            logger.removeHandler(handler)


def _assert_masked(records: _Records, events: list[MutableMapping[str, Any]]) -> None:
    assert SENTINEL not in "\n".join(records.lines)
    assert SENTINEL not in repr(events)
    assert any(
        event["event"] == "realtime.database_error" and event.get("error_type") == "DBAPIError"
        for event in events
    )


async def _receive_type(ws: ClientConnection, event_type: str) -> dict[str, Any]:
    while True:
        event = await receive(ws)
        if event.get("type") == event_type:
            return event


async def _close_code(ws: ClientConnection) -> int | None:
    async def drain() -> None:
        with contextlib.suppress(ConnectionClosed):
            while True:
                await ws.recv()

    await asyncio.wait_for(drain(), timeout=5)
    return ws.close_code


async def test_typing_database_error_is_masked_and_reported_as_unavailable(
    live_server: str,
    make_messaging_care: MakeMessagingCare,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    care = await make_messaging_care(
        "db-typing-doctor@example.com", "db-typing-patient@example.com"
    )
    cookie = await session_cookie(live_server, care.patient.email)
    monkeypatch.setattr(presence, "set_typing", _raise_database_error)
    command = {
        "patient_id": str(care.patient.id),
        "expected_doctor_id": str(care.doctor.id),
        "active": True,
    }

    with _captured_logs() as (records, events):
        async with ws_connect(live_server, cookie) as patient:
            assert (await receive(patient))["type"] == "ready"
            await patient.send(json.dumps({"type": "message.typing", "data": command}))
            error = await asyncio.wait_for(_receive_type(patient, "message.error"), timeout=5)
            assert error["data"] == {"code": "messaging_unavailable"}
            # Сокет остаётся рабочим: следующая команда получает обычный ответ.
            await patient.send(json.dumps({"type": "unknown", "data": {}}))
            next_error = await asyncio.wait_for(_receive_type(patient, "message.error"), timeout=5)
            assert next_error["data"] == {"code": "invalid_command"}

    _assert_masked(records, events)


async def test_heartbeat_database_error_closes_socket_with_internal_error(
    live_server: str,
    make_messaging_care: MakeMessagingCare,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    care = await make_messaging_care("db-beat-doctor@example.com", "db-beat-patient@example.com")
    cookie = await session_cookie(live_server, care.patient.email)
    monkeypatch.setattr(realtime_api, "HEARTBEAT_SECONDS", 0.1)
    original_touch = presence.touch
    touches: list[str] = []

    async def fail_on_heartbeat(*args: Any, **kwargs: Any) -> None:
        touches.append("touch")
        if len(touches) > 1:
            raise _database_error()
        await original_touch(*args, **kwargs)

    monkeypatch.setattr(presence, "touch", fail_on_heartbeat)

    with _captured_logs() as (records, events):
        async with ws_connect(live_server, cookie) as patient:
            assert (await receive(patient))["type"] == "ready"
            assert await _close_code(patient) == 1011

    assert len(touches) == 2
    _assert_masked(records, events)


async def test_handshake_database_error_is_masked_as_service_unavailable(
    live_server: str,
    make_messaging_care: MakeMessagingCare,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    care = await make_messaging_care("db-open-doctor@example.com", "db-open-patient@example.com")
    cookie = await session_cookie(live_server, care.patient.email)
    monkeypatch.setattr(identity, "get_active_session", _raise_database_error)

    with _captured_logs() as (records, events), pytest.raises(InvalidStatus) as rejected:
        async with ws_connect(live_server, cookie):
            pass

    assert rejected.value.response.status_code == 503
    _assert_masked(records, events)
