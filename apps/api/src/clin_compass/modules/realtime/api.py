"""WebSocket команды и policy checks для сообщений и присутствия."""

import asyncio
import contextlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import structlog
from fastapi import APIRouter, FastAPI, Response, WebSocket, WebSocketDisconnect
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from clin_compass.core.origin import is_same_origin
from clin_compass.core.realtime import SESSION_ENDED, DeliveryGuard, Hub
from clin_compass.modules.identity import service as identity
from clin_compass.modules.identity.deps import SESSION_COOKIE
from clin_compass.modules.identity.schemas import ActiveSession, UserOut
from clin_compass.modules.messaging import service as messaging
from clin_compass.modules.messaging.schemas import TYPING_MIN_INTERVAL_SECONDS, TypingCommand

router = APIRouter(tags=["realtime"])
log = structlog.get_logger()

# Код закрытия «нарушение политики»: до accept Starlette отвечает на него HTTP 403.
POLICY_VIOLATION = 1008
INVALID_COMMAND = {"type": "message.error", "data": {"code": "invalid_command"}}
# Сбой БД при проверке сессии или присутствия: сокет закрывается, клиент переподключится.
INTERNAL_ERROR = 1011
# Сбой БД при выполнении команды: тот же код, что у REST 503.
MESSAGING_UNAVAILABLE = "messaging_unavailable"
TRANSPORT_MAX_BYTES = 4096
HEARTBEAT_SECONDS = 20


class _TypingThrottle:
    """Лимит `active=true` на подключение и пару, который срабатывает до обращений к БД."""

    def __init__(self, interval: float) -> None:
        self._interval = interval
        self._accepted: dict[tuple[UUID, UUID], float] = {}

    def allow(self, command: TypingCommand, now: float) -> bool:
        """Пропустить `active=false` всегда, `active=true` — раз в интервал на пару."""
        if not command.active:
            return True
        # Отметки старше интервала уже ничего не ограничивают: словарь не растёт без предела.
        self._accepted = {
            pair: accepted_at
            for pair, accepted_at in self._accepted.items()
            if now - accepted_at < self._interval
        }
        pair = (command.patient_id, command.expected_doctor_id)
        if pair in self._accepted:
            return False
        self._accepted[pair] = now
        return True


async def _active_session(websocket: WebSocket, token: str) -> ActiveSession | None:
    async with websocket.app.state.sessionmaker() as session:
        return await identity.get_active_session(session, token)


def build_delivery_guard(app: FastAPI) -> DeliveryGuard:
    """Bind the generic core Hub to public identity and messaging services."""

    async def guard(
        user_id: UUID, session_key: str, _event_type: str, data: dict[str, Any]
    ) -> bool:
        if not isinstance(data, dict):
            return False
        try:
            patient_id = UUID(str(data["patient_id"]))
            doctor_id = UUID(str(data["doctor_id"]))
        except (KeyError, TypeError, ValueError):
            return False
        async with app.state.sessionmaker() as session:
            active = await identity.get_active_session_by_key(session, session_key)
            if active is None or active.user.id != user_id:
                return False
            return await messaging.can_deliver_event(
                session,
                recipient_id=user_id,
                patient_id=patient_id,
                doctor_id=doctor_id,
            )

    return guard


def _log_database_error(error: SQLAlchemyError) -> None:
    # Текст ошибки SQLAlchemy содержит SQL и параметры: ID пользователей, хеш сессии.
    log.warning("realtime.database_error", error_type=type(error).__name__)


async def _close_unavailable(hub: Hub, websocket: WebSocket) -> None:
    hub.disconnect(websocket)
    with contextlib.suppress(WebSocketDisconnect, RuntimeError):
        await websocket.close(code=INTERNAL_ERROR)


async def _end_session(hub: Hub, websocket: WebSocket) -> None:
    hub.disconnect(websocket)
    # Сокет мог закрыться раньше: клиент ушёл или его уже закрыл хаб.
    with contextlib.suppress(WebSocketDisconnect, RuntimeError):
        await websocket.close(code=SESSION_ENDED)


async def _next_input(
    websocket: WebSocket, disconnected: asyncio.Event, wait_seconds: float
) -> Mapping[str, Any] | None:
    """Wait for one ASGI frame, Hub removal, or the next presence heartbeat."""
    receive_task = asyncio.create_task(websocket.receive())
    disconnected_task = asyncio.create_task(disconnected.wait())
    done, pending = await asyncio.wait(
        (receive_task, disconnected_task),
        timeout=wait_seconds,
        return_when=asyncio.FIRST_COMPLETED,
    )
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    if disconnected_task in done and disconnected_task.result():
        return None
    if receive_task in done:
        return receive_task.result()
    return {}


async def _refresh_presence(
    websocket: WebSocket,
    actor: UserOut,
    *,
    connection_id: UUID,
    session_key: str,
    expires_at: datetime,
) -> None:
    async with websocket.app.state.sessionmaker() as session:
        await messaging.touch_presence(
            session,
            actor,
            connection_id=connection_id,
            session_key=session_key,
            session_expires_at=expires_at,
        )
        await session.commit()


async def _disconnect_presence(
    websocket: WebSocket, actor: UserOut, *, connection_id: UUID
) -> None:
    try:
        async with websocket.app.state.sessionmaker() as session:
            await messaging.disconnect_presence(session, actor, connection_id=connection_id)
            await session.commit()
    except SQLAlchemyError as error:
        log.warning("realtime.presence_cleanup_failed", error_type=type(error).__name__)


async def _handle_typing(
    websocket: WebSocket,
    actor: UserOut,
    command: TypingCommand,
    *,
    connection_id: UUID,
    session_key: str,
) -> str | None:
    try:
        async with websocket.app.state.sessionmaker() as session:
            try:
                await messaging.update_typing(
                    session,
                    actor,
                    connection_id=connection_id,
                    session_key=session_key,
                    patient_id=command.patient_id,
                    expected_doctor_id=command.expected_doctor_id,
                    active=command.active,
                )
                await session.commit()
                return None
            except messaging.MessagingError as error:
                await session.rollback()
                return error.code
    except SQLAlchemyError as error:
        # Не выполнена только команда: проверенная сессия и сокет остаются рабочими.
        _log_database_error(error)
        return MESSAGING_UNAVAILABLE


async def _send_invalid(websocket: WebSocket) -> None:
    await websocket.send_json(INVALID_COMMAND)


@router.websocket("/ws")
async def events(websocket: WebSocket) -> None:
    token = websocket.cookies.get(SESSION_COOKIE)
    active = None
    if token and is_same_origin(websocket.headers):
        try:
            active = await _active_session(websocket, token)
        except SQLAlchemyError as error:
            _log_database_error(error)
            # До accept код закрытия не передать, а close дал бы 403 «нет доступа».
            await websocket.send_denial_response(Response(status_code=503))
            return
    if token is None or active is None:
        await websocket.close(code=POLICY_VIOLATION)
        return

    await websocket.accept()
    hub: Hub = websocket.app.state.hub
    actor = active.user
    connection_id = uuid4()
    disconnected = hub.connect(actor.id, websocket, session=active.key)
    try:
        # Выход между первой проверкой и регистрацией в хабе не застал этот сокет:
        # его NOTIFY закрывать было нечего. Поэтому после регистрации проверяем ещё раз.
        active = await _active_session(websocket, token)
        if active is None:
            await _end_session(hub, websocket)
            return
        await websocket.send_json({"type": "ready", "data": {}})
        await _refresh_presence(
            websocket,
            active.user,
            connection_id=connection_id,
            session_key=active.key,
            expires_at=active.expires_at,
        )
        loop = asyncio.get_running_loop()
        heartbeat_deadline = loop.time() + HEARTBEAT_SECONDS
        typing_throttle = _TypingThrottle(TYPING_MIN_INTERVAL_SECONDS)
        while True:
            remaining = (active.expires_at - datetime.now(UTC)).total_seconds()
            if remaining <= 0:
                await _end_session(hub, websocket)
                return
            heartbeat_remaining = max(0.0, heartbeat_deadline - loop.time())
            incoming = await _next_input(
                websocket, disconnected, min(heartbeat_remaining, remaining)
            )
            if incoming is None:
                return
            if incoming == {} or loop.time() >= heartbeat_deadline:
                active = await _active_session(websocket, token)
                if active is None:
                    await _end_session(hub, websocket)
                    return
                await _refresh_presence(
                    websocket,
                    active.user,
                    connection_id=connection_id,
                    session_key=active.key,
                    expires_at=active.expires_at,
                )
                heartbeat_deadline = loop.time() + HEARTBEAT_SECONDS
                if incoming == {}:
                    continue
            if incoming.get("type") == "websocket.disconnect":
                return
            if incoming.get("bytes") is not None:
                await websocket.close(code=1003)
                return
            raw = incoming.get("text")
            if not isinstance(raw, str):
                await _send_invalid(websocket)
                continue
            if len(raw.encode("utf-8")) > TRANSPORT_MAX_BYTES:
                # The actual pre-parse bound is Uvicorn ws_max_size=4096.
                await websocket.close(code=1009)
                return
            try:
                envelope = json.loads(raw)
            except json.JSONDecodeError:
                await _send_invalid(websocket)
                continue
            if (
                not isinstance(envelope, dict)
                or set(envelope) != {"type", "data"}
                or envelope.get("type") != "message.typing"
            ):
                await _send_invalid(websocket)
                continue
            try:
                command = TypingCommand.model_validate(envelope["data"])
            except (TypeError, ValidationError):
                await _send_invalid(websocket)
                continue
            if not typing_throttle.allow(command, loop.time()):
                # Лишний ввод игнорируется молча (спека, раздел 5), без проверки сессии и SQL.
                continue

            active = await _active_session(websocket, token)
            if active is None:
                await _end_session(hub, websocket)
                return
            error_code = await _handle_typing(
                websocket,
                active.user,
                command,
                connection_id=connection_id,
                session_key=active.key,
            )
            if error_code is not None:
                await websocket.send_json({"type": "message.error", "data": {"code": error_code}})
    except WebSocketDisconnect:
        return
    except SQLAlchemyError as error:
        # Сессию или аренду присутствия не проверить: держать сокет открытым нельзя.
        _log_database_error(error)
        await _close_unavailable(hub, websocket)
        return
    finally:
        hub.disconnect(websocket)
        await _disconnect_presence(websocket, actor, connection_id=connection_id)
