"""Авторизованное воспроизведение голосовых сообщений с поддержкой byte ranges."""

import re
from collections.abc import AsyncIterator
from functools import partial
from typing import BinaryIO
from uuid import UUID

import anyio
import structlog
from botocore.exceptions import BotoCoreError
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from clin_compass.core.deps import DbSession
from clin_compass.core.errors import PROBLEM_JSON, ProblemOut, problem
from clin_compass.core.storage import S3Storage, StorageError
from clin_compass.modules.identity import service as identity
from clin_compass.modules.messaging import service
from clin_compass.modules.messaging.deps import MessagingUser
from clin_compass.modules.messaging.models import Conversation, Message

router = APIRouter(tags=["messaging"])
log = structlog.get_logger()
_RANGE = re.compile(r"bytes=(\d*)-(\d*)", re.IGNORECASE)
_AUDIO_CONTENT = {
    "audio/webm": {"schema": {"type": "string", "format": "binary"}},
    "audio/mp4": {"schema": {"type": "string", "format": "binary"}},
}


def _error(status: int, detail: str, code: str) -> JSONResponse:
    response = problem(status, detail, code=code)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Accept-Ranges"] = "bytes"
    return response


def _range(value: str | None, size: int) -> tuple[str | None, int, int, bool]:
    """Вернуть S3 range, включительные границы и признак неудовлетворимого диапазона."""
    if value is None or "," in value:
        return None, 0, max(size - 1, 0), False
    match = _RANGE.fullmatch(value.strip())
    if match is None or (not match.group(1) and not match.group(2)):
        return None, 0, max(size - 1, 0), False
    first, last = match.groups()
    try:
        if not first:
            suffix = int(last)
            if suffix <= 0 or size <= 0:
                return None, 0, 0, True
            start = max(size - suffix, 0)
            end = size - 1
        else:
            start = int(first)
            end = int(last) if last else size - 1
            if last and end < start:
                # Конец раньше начала — некорректный синтаксис: отдаём файл целиком.
                return None, 0, max(size - 1, 0), False
            if size <= 0 or start >= size:
                return None, 0, 0, True
            end = min(end, size - 1)
    except ValueError:
        return None, 0, max(size - 1, 0), False
    return f"bytes={start}-{end}", start, end, False


async def _stream(body: BinaryIO, length: int) -> AsyncIterator[bytes]:
    remaining = length
    try:
        while remaining > 0:
            chunk = await anyio.to_thread.run_sync(body.read, min(64 * 1024, remaining))
            if not chunk:
                return
            remaining -= len(chunk)
            yield chunk
    except (BotoCoreError, OSError) as error:
        # Ошибка тела SDK не должна содержать ответ RustFS или ключ объекта в логах.
        log.warning("messaging.audio_stream_failed", error_type=type(error).__name__)
    finally:
        with anyio.CancelScope(shield=True):
            try:
                await anyio.to_thread.run_sync(body.close)
            except (BotoCoreError, OSError) as error:
                log.warning("messaging.audio_stream_close_failed", error_type=type(error).__name__)


@router.get(
    "/patients/{patient_id}/messages/{message_id}/audio",
    response_class=StreamingResponse,
    responses={
        200: {"description": "Private audio stream", "content": _AUDIO_CONTENT},
        206: {"description": "Requested byte range", "content": _AUDIO_CONTENT},
        401: {
            "description": "Authentication is required",
            "content": {PROBLEM_JSON: {"schema": ProblemOut.model_json_schema()}},
        },
        403: {
            "description": "The authenticated role is not allowed",
            "content": {PROBLEM_JSON: {"schema": ProblemOut.model_json_schema()}},
        },
        404: {
            "description": "Audio is not available",
            "content": {PROBLEM_JSON: {"schema": ProblemOut.model_json_schema()}},
        },
        416: {
            "description": "The range cannot be satisfied",
            "content": {PROBLEM_JSON: {"schema": ProblemOut.model_json_schema()}},
        },
        503: {
            "description": "Private audio storage is unavailable",
            "content": {PROBLEM_JSON: {"schema": ProblemOut.model_json_schema()}},
        },
    },
)
async def get_audio(
    patient_id: UUID,
    message_id: UUID,
    request: Request,
    user: MessagingUser,
    session: DbSession,
) -> Response:
    """Отдать аудио только участнику текущей пары и закрыть SDK-поток при завершении."""
    try:
        doctor_id = await service._lock_current_pair(
            session, user, patient_id, expected_doctor_id=None
        )
        row = (
            await session.execute(
                select(Message, Conversation)
                .join(Conversation, Conversation.id == Message.conversation_id)
                .where(
                    Message.id == message_id,
                    Message.kind == "voice",
                    Conversation.patient_id == patient_id,
                    Conversation.doctor_id == doctor_id,
                )
            )
        ).one_or_none()
        if row is None:
            raise service.MessagingError(404, "not_found", "Audio not found")
        message, _ = row
        if message.audio_key is None or message.audio_size is None:
            raise service.MessagingError(404, "not_found", "Audio not found")
        await identity.log_access(
            session,
            actor_id=user.id,
            action="messaging.audio",
            patient_id=patient_id,
            subject=str(message.id),
        )
        await session.commit()
    except service.MessagingError as error:
        await service.audit_denied(
            session, actor_id=user.id, patient_id=patient_id, action="messaging.audio"
        )
        return _error(error.status, error.detail, error.code)
    except SQLAlchemyError as error:
        await session.rollback()
        log.error("messaging.database_error", error_type=type(error).__name__)
        await service.audit_denied(
            session, actor_id=user.id, patient_id=patient_id, action="messaging.audio"
        )
        return _error(503, "Messaging is temporarily unavailable", "messaging_unavailable")

    selection, start, end, unsatisfiable = _range(request.headers.get("range"), message.audio_size)
    if unsatisfiable:
        response = _error(416, "Requested byte range is not satisfiable", "range_not_satisfiable")
        response.headers["Content-Range"] = f"bytes */{message.audio_size}"
        return response

    storage: S3Storage | None = getattr(request.app.state, "storage", None)
    if storage is None:
        return _error(503, "Private audio storage is unavailable", "storage_unavailable")
    try:
        stored = await anyio.to_thread.run_sync(
            partial(storage.get, message.audio_key, byte_range=selection)
        )
    except StorageError as error:
        log.warning("messaging.storage_unavailable", error_type=type(error).__name__)
        return _error(503, "Private audio storage is unavailable", "storage_unavailable")

    is_partial = selection is not None
    length = end - start + 1 if is_partial else message.audio_size
    headers = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
        "Content-Disposition": 'inline; filename="voice-message.webm"'
        if message.audio_mime == "audio/webm"
        else 'inline; filename="voice-message.mp4"',
        "Content-Length": str(length),
    }
    if is_partial:
        headers["Content-Range"] = f"bytes {start}-{end}/{message.audio_size}"
    return StreamingResponse(
        _stream(stored.body, length),
        status_code=206 if is_partial else 200,
        media_type=message.audio_mime,
        headers=headers,
    )
