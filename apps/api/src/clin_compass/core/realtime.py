"""События реального времени: Postgres NOTIFY → WebSocket адресатов.

`publish` вызывается в транзакции бизнес-записи. Postgres доставляет NOTIFY только
при коммите, поэтому откат не рассылает ничего. Каждый экземпляр api слушает канал
отдельным соединением и отправляет событие своим подключённым адресатам.

Тем же каналом идёт служебное событие о конце сессии: каждый экземпляр закрывает
сокеты этой сессии, клиенту оно не пересылается.
"""

import asyncio
import contextlib
import json
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any, Protocol
from uuid import UUID

import asyncpg
import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.websockets import WebSocketDisconnect

from clin_compass.core.queue import asyncpg_dsn

CHANNEL = "compass_events"
# Лимит NOTIFY в Postgres — 8000 байт. В событии — только ссылки, не данные.
MAX_PAYLOAD_BYTES = 7900
# Код закрытия «сессия закончилась» (выход или истечение): клиенту не нужно
# переподключаться, нужно на вход. Коды 4000–4999 отданы приложениям.
SESSION_ENDED = 4401

log = structlog.get_logger()


class Socket(Protocol):
    async def send_json(self, data: Any) -> None: ...

    async def close(self, code: int = 1000) -> None: ...


DeliveryGuard = Callable[[UUID, str, str, dict[str, Any]], Awaitable[bool]]


async def publish(
    session: AsyncSession, user_ids: list[UUID], event_type: str, data: dict[str, Any]
) -> None:
    payload = json.dumps(
        {"to": [str(u) for u in user_ids], "type": event_type, "data": data},
        ensure_ascii=False,
    )
    if len(payload.encode()) > MAX_PAYLOAD_BYTES:
        raise ValueError(f"Realtime payload too large: {len(payload.encode())} bytes")
    await _notify(session, payload)


async def publish_session_ended(session: AsyncSession, session_key: str) -> None:
    """Закрыть сокеты сессии на всех экземплярах api. Уходит при коммите транзакции."""
    await _notify(session, json.dumps({"session_ended": session_key}))


async def _notify(session: AsyncSession, payload: str) -> None:
    await session.execute(select(func.pg_notify(CHANNEL, payload)))


class Hub:
    """WebSocket-подключения этого экземпляра api по пользователю и по сессии.

    Событие отправляется всем адресатам параллельно. Сокет, который отвалился или
    не принял сообщение за `send_timeout`, удаляется: клиент переподключится и
    дочитает пропущенное по REST. Конец сессии закрывает её сокеты кодом
    `SESSION_ENDED`. Сессия — ключ из identity (хеш токена).
    """

    def __init__(
        self,
        send_timeout: float = 5.0,
        delivery_guard: DeliveryGuard | None = None,
    ) -> None:
        self._sockets: dict[UUID, set[Socket]] = defaultdict(set)
        self._sessions: dict[str, set[Socket]] = defaultdict(set)
        self._owners: dict[Socket, tuple[UUID, str]] = {}
        self._disconnected: dict[Socket, asyncio.Event] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._send_timeout = send_timeout
        self._delivery_guard = delivery_guard

    def connect(self, user_id: UUID, socket: Socket, *, session: str) -> asyncio.Event:
        self._sockets[user_id].add(socket)
        self._sessions[session].add(socket)
        self._owners[socket] = (user_id, session)
        removed = asyncio.Event()
        self._disconnected[socket] = removed
        return removed

    def disconnect(self, socket: Socket) -> None:
        owner = self._owners.pop(socket, None)
        if owner is None:
            return
        removed = self._disconnected.pop(socket, None)
        if removed is not None:
            removed.set()
        user_id, session = owner
        _discard(self._sockets, user_id, socket)
        _discard(self._sessions, session, socket)

    def connected_users(self) -> set[UUID]:
        return set(self._sockets)

    async def dispatch(self, raw: str) -> None:
        event = json.loads(raw)
        if "session_ended" in event:
            await self._close_session(event["session_ended"])
            return
        message = {"type": event["type"], "data": event["data"]}
        targets = [
            (UUID(user_id), socket)
            for user_id in event["to"]
            for socket in list(self._sockets.get(UUID(user_id), ()))
        ]
        await asyncio.gather(*(self._send(user_id, socket, message) for user_id, socket in targets))

    async def _send(self, user_id: UUID, socket: Socket, message: dict[str, Any]) -> None:
        try:
            async with asyncio.timeout(self._send_timeout):
                event_type = message["type"]
                if event_type.startswith("message.") and self._delivery_guard is not None:
                    owner = self._owners.get(socket)
                    if owner is None:
                        return
                    try:
                        allowed = await self._delivery_guard(
                            user_id, owner[1], event_type, message["data"]
                        )
                    except Exception as error:  # noqa: BLE001
                        # Identity or pair checks failing means delivery must fail closed.
                        log.warning(
                            "realtime.delivery_guard_failed",
                            user_id=str(user_id),
                            error=type(error).__name__,
                        )
                        self.disconnect(socket)
                        return
                    if not allowed:
                        return
                await socket.send_json(message)
        except (TimeoutError, WebSocketDisconnect, RuntimeError, OSError) as error:
            log.warning("realtime.send_failed", user_id=str(user_id), error=type(error).__name__)
            self.disconnect(socket)

    async def _close_session(self, session: str) -> None:
        sockets = list(self._sessions.get(session, ()))
        # Убираем до закрытия: события, пришедшие следом, в эти сокеты уже не пойдут.
        for socket in sockets:
            self.disconnect(socket)
        await asyncio.gather(*(self._close(socket) for socket in sockets))

    async def _close(self, socket: Socket) -> None:
        try:
            async with asyncio.timeout(self._send_timeout):
                await socket.close(code=SESSION_ENDED)
        except (TimeoutError, WebSocketDisconnect, RuntimeError, OSError) as error:
            # Сокет уже закрыт или завис: из хаба он убран, события в него не пойдут.
            log.warning("realtime.close_failed", error=type(error).__name__)

    def on_notify(self, _connection: object, _pid: int, _channel: str, payload: str) -> None:
        """Колбэк asyncpg: рассылка в отдельной задаче, ссылка на неё хранится до конца."""
        task = asyncio.create_task(self.dispatch(payload))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)


def _discard[K](groups: dict[K, set[Socket]], key: K, socket: Socket) -> None:
    sockets = groups.get(key)
    if sockets is None:
        return
    sockets.discard(socket)
    if not sockets:
        del groups[key]


class Listener:
    """LISTEN в фоне с переподключением.

    api стартует и при недоступной БД (health тогда отвечает 503). Если соединение
    оборвалось, слушатель переподключается; события за время обрыва теряются, и клиент
    дочитывает пропущенное по REST.
    """

    def __init__(self, database_url: str, hub: Hub, retry_delay: float = 1.0) -> None:
        self._dsn = asyncpg_dsn(database_url)
        self._hub = hub
        self._retry_delay = retry_delay
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def _run(self) -> None:
        while True:
            try:
                await self._listen_until_lost()
            except (OSError, asyncpg.PostgresError, asyncpg.InterfaceError) as error:
                log.warning("realtime.listener_unavailable", error=str(error))
            await asyncio.sleep(self._retry_delay)

    async def _listen_until_lost(self) -> None:
        lost = asyncio.Event()
        connection = await asyncpg.connect(self._dsn)
        try:
            connection.add_termination_listener(lambda _: lost.set())
            await connection.add_listener(CHANNEL, self._hub.on_notify)
            log.info("realtime.listener_connected")
            await lost.wait()
            log.warning("realtime.listener_lost")
        finally:
            if not connection.is_closed():
                await connection.close()
