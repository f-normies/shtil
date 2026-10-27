"""HTTP-потоки закрытого аудио дневника с поддержкой одиночного byte range."""

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
from sqlalchemy.exc import SQLAlchemyError

from clin_compass.core.deps import DbSession
from clin_compass.core.errors import problem
from clin_compass.core.storage import S3Storage, StorageError
from clin_compass.modules.diary import service
from clin_compass.modules.diary.errors import DiaryError
from clin_compass.modules.diary.service import AudioReference
from clin_compass.modules.identity.deps import CurrentUser

router = APIRouter(tags=["diary"])
log = structlog.get_logger()
_RANGE = re.compile(r"bytes=(\d*)-(\d*)", re.IGNORECASE)


def _error(status: int, detail: str, code: str) -> JSONResponse:
    response = problem(status, detail, code=code)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Accept-Ranges"] = "bytes"
    return response


def _range(value: str | None, size: int) -> tuple[str | None, int, int, bool]:
    """Parse one inclusive byte range; ignore malformed or multipart range requests."""
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
        log.warning("diary.audio_stream_failed", error_type=type(error).__name__)
    finally:
        with anyio.CancelScope(shield=True):
            try:
                await anyio.to_thread.run_sync(body.close)
            except (BotoCoreError, OSError) as error:
                log.warning("diary.audio_stream_close_failed", error_type=type(error).__name__)


async def _response(request: Request, audio: AudioReference) -> Response:
    selection, start, end, unsatisfiable = _range(request.headers.get("range"), audio.byte_size)
    if unsatisfiable:
        response = _error(416, "Requested byte range is not satisfiable", "range_not_satisfiable")
        response.headers["Content-Range"] = f"bytes */{audio.byte_size}"
        return response

    storage: S3Storage | None = getattr(request.app.state, "storage", None)
    if storage is None:
        return _error(503, "Private audio storage is unavailable", "storage_unavailable")
    try:
        stored = await anyio.to_thread.run_sync(
            partial(storage.get, audio.storage_key, byte_range=selection)
        )
    except StorageError as error:
        log.warning("diary.audio_storage_unavailable", error_type=type(error).__name__)
        return _error(503, "Private audio storage is unavailable", "storage_unavailable")

    partial_response = selection is not None
    length = end - start + 1 if partial_response else audio.byte_size
    if stored.size != length:
        with anyio.CancelScope(shield=True):
            await anyio.to_thread.run_sync(stored.body.close)
        log.warning("diary.audio_size_mismatch")
        return _error(503, "Private audio storage is unavailable", "storage_unavailable")
    headers = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "no-store",
        "Content-Disposition": 'inline; filename="diary-audio.webm"'
        if audio.mime_type == "audio/webm"
        else 'inline; filename="diary-audio.mp4"',
        "Content-Length": str(length),
        "X-Content-Type-Options": "nosniff",
    }
    if partial_response:
        headers["Content-Range"] = f"bytes {start}-{end}/{audio.byte_size}"
    return StreamingResponse(
        _stream(stored.body, length),
        status_code=206 if partial_response else 200,
        media_type=audio.mime_type,
        headers=headers,
    )


@router.get("/diary/entries/{entry_id}/audio", response_class=StreamingResponse)
async def get_patient_audio(
    entry_id: UUID,
    request: Request,
    actor: CurrentUser,
    session: DbSession,
) -> Response:
    """Вернуть поток только владельцу текущей видимой записи."""
    try:
        audio = await service.patient_audio(session, actor, entry_id)
    except DiaryError as error:
        return _error(error.status, error.detail, error.code)
    except SQLAlchemyError as error:
        await session.rollback()
        log.error("diary.audio_database_error", error_type=type(error).__name__)
        return _error(503, "Diary is temporarily unavailable", "diary_unavailable")
    return await _response(request, audio)


@router.get(
    "/patients/{patient_id}/diary/{entry_id}/audio",
    response_class=StreamingResponse,
)
async def get_doctor_audio(
    patient_id: UUID,
    entry_id: UUID,
    request: Request,
    actor: CurrentUser,
    session: DbSession,
) -> Response:
    """Вернуть поток только текущему ведущему врачу указанного пациента."""
    try:
        audio = await service.doctor_audio(session, actor, patient_id, entry_id)
    except DiaryError as error:
        return _error(error.status, error.detail, error.code)
    except SQLAlchemyError as error:
        await session.rollback()
        log.error("diary.audio_database_error", error_type=type(error).__name__)
        return _error(503, "Diary is temporarily unavailable", "diary_unavailable")
    return await _response(request, audio)
