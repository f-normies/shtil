"""Хаб: один плохой сокет не мешает доставке остальным (путь алерта, спека 4.5).

Конец сессии закрывает только её сокеты (#14).
"""

import asyncio
import json
from typing import Any
from uuid import UUID, uuid4

from starlette.websockets import WebSocketDisconnect

from clin_compass.core.realtime import Hub


class RecordingSocket:
    def __init__(self) -> None:
        self.received: list[Any] = []
        self.got = asyncio.Event()
        self.closed_with: int | None = None

    async def send_json(self, data: Any) -> None:
        self.received.append(data)
        self.got.set()

    async def close(self, code: int = 1000) -> None:
        self.closed_with = code


class DisconnectedSocket:
    async def send_json(self, data: Any) -> None:
        raise WebSocketDisconnect(code=1006)

    async def close(self, code: int = 1000) -> None:
        raise RuntimeError("Cannot call close once a close message has been sent")


class StalledSocket:
    async def send_json(self, data: Any) -> None:
        await asyncio.sleep(3600)

    async def close(self, code: int = 1000) -> None:
        await asyncio.sleep(3600)


def event(to: list[UUID]) -> str:
    return json.dumps({"to": [str(u) for u in to], "type": "alert.created", "data": {}})


async def test_disconnected_socket_does_not_block_delivery_and_is_dropped() -> None:
    hub = Hub()
    dead_user, live_user = uuid4(), uuid4()
    dead, live = DisconnectedSocket(), RecordingSocket()
    hub.connect(dead_user, dead, session="dead")
    hub.connect(live_user, live, session="live")

    await hub.dispatch(event([dead_user, live_user]))
    await hub.dispatch(event([dead_user]))

    assert live.received == [{"type": "alert.created", "data": {}}]
    assert hub.connected_users() == {live_user}


async def test_stalled_socket_does_not_delay_other_recipients() -> None:
    hub = Hub(send_timeout=0.2)
    slow_user, fast_user = uuid4(), uuid4()
    fast = RecordingSocket()
    hub.connect(slow_user, StalledSocket(), session="slow")
    hub.connect(fast_user, fast, session="fast")

    dispatching = asyncio.create_task(hub.dispatch(event([slow_user, fast_user])))
    await asyncio.wait_for(fast.got.wait(), timeout=0.1)
    await asyncio.wait_for(dispatching, timeout=1)

    assert fast.received == [{"type": "alert.created", "data": {}}]
    # Зависший сокет считаем потерянным: клиент переподключится и дочитает по REST.
    assert hub.connected_users() == {fast_user}


def session_ended(session: str) -> str:
    return json.dumps({"session_ended": session})


async def test_session_end_closes_only_sockets_of_that_session() -> None:
    hub = Hub()
    user = uuid4()
    laptop, phone = RecordingSocket(), RecordingSocket()
    hub.connect(user, laptop, session="laptop")
    hub.connect(user, phone, session="phone")

    await hub.dispatch(session_ended("laptop"))
    await hub.dispatch(event([user]))

    # 4401 — договорённость с клиентом: сессия закончилась, переподключаться не нужно.
    assert laptop.closed_with == 4401
    assert laptop.received == []
    assert phone.closed_with is None
    assert phone.received == [{"type": "alert.created", "data": {}}]


async def test_session_end_survives_sockets_that_fail_or_stall_on_close() -> None:
    hub = Hub(send_timeout=0.2)
    user = uuid4()
    hub.connect(user, DisconnectedSocket(), session="ended")
    hub.connect(user, StalledSocket(), session="ended")

    await asyncio.wait_for(hub.dispatch(session_ended("ended")), timeout=1)

    assert hub.connected_users() == set()
