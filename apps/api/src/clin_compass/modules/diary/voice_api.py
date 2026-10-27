"""Ограниченный multipart endpoint для аудиозаписей дневника."""

import hashlib
import json
from collections import Counter
from uuid import UUID

import structlog
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from starlette.datastructures import FormData, UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.formparsers import MultiPartException
from starlette.requests import Request as StarletteRequest

from clin_compass.core.deps import AppSettings, DbSession
from clin_compass.core.errors import problem
from clin_compass.core.storage import S3Storage, StorageError
from clin_compass.modules.diary import service, voice
from clin_compass.modules.diary.audio_format import is_supported_audio
from clin_compass.modules.diary.schemas import (
    DiaryAudioCreateIn,
    DiaryCommandReceipt,
    DiaryTranscriptionRetryIn,
    DiaryTranscriptionRetryOut,
    DiaryVoiceEntryCreateIn,
    DiaryVoiceUploadMetadataIn,
)
from clin_compass.modules.identity.deps import CurrentUser

MAX_AUDIO_BYTES = 25 * 1024 * 1024
MAX_MULTIPART_BYTES = 26 * 1024 * 1024
router = APIRouter(tags=["diary"])
log = structlog.get_logger()


def _error(status: int, detail: str, code: str) -> JSONResponse:
    response = problem(status, detail, code=code)
    response.headers["Cache-Control"] = "no-store"
    return response


async def _read_limited_body(request: Request) -> bytes:
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_MULTIPART_BYTES:
            raise ValueError("multipart body too large")
        chunks.append(chunk)
    return b"".join(chunks)


def _replay_request(request: Request, body: bytes) -> StarletteRequest:
    delivered = False

    async def receive() -> dict[str, object]:
        nonlocal delivered
        if delivered:
            return {"type": "http.request", "body": b"", "more_body": False}
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    return StarletteRequest(request.scope, receive=receive)


def _parse_parts(form: FormData) -> tuple[str, UploadFile]:
    parts = form.multi_items()
    if len(parts) != 2 or Counter(name for name, _ in parts) != Counter({"metadata": 1, "file": 1}):
        raise ValueError("unexpected multipart fields")
    values = dict(parts)
    metadata = values["metadata"]
    upload = values["file"]
    if not isinstance(metadata, str) or not isinstance(upload, UploadFile):
        raise ValueError("invalid multipart fields")
    return metadata, upload


def _parse_metadata(raw: str) -> tuple[DiaryVoiceEntryCreateIn, int]:
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("metadata must be an object")
    upload = DiaryVoiceUploadMetadataIn.model_validate(parsed)
    body = DiaryVoiceEntryCreateIn.model_validate(upload.model_dump(exclude={"duration_ms"}))
    return body, upload.duration_ms


_VOICE_UPLOAD_OPENAPI = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": ["metadata", "file"],
                    "properties": {
                        "metadata": {"$ref": "#/components/schemas/DiaryVoiceUploadMetadataIn"},
                        "file": {"type": "string", "format": "binary"},
                    },
                },
                "encoding": {
                    "metadata": {"contentType": "application/json"},
                    "file": {"contentType": "audio/webm, audio/mp4"},
                },
            }
        },
    }
}


@router.post(
    "/diary/entries/voice",
    response_model=DiaryCommandReceipt,
    status_code=201,
    responses={200: {"model": DiaryCommandReceipt, "description": "Idempotent replay"}},
    openapi_extra=_VOICE_UPLOAD_OPENAPI,
)
async def post_voice(
    request: Request,
    response: Response,
    actor: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> DiaryCommandReceipt | JSONResponse:
    """Accept one bounded WebM or MP4 recording and enqueue transcription atomically."""
    await service.validate_voice_actor(session, actor)
    await session.rollback()

    form: FormData | None = None
    try:
        raw_body = await _read_limited_body(request)
    except ValueError:
        return _error(413, "Audio upload exceeds the allowed size", "audio_too_large")
    try:
        form = await _replay_request(request, raw_body).form(
            max_files=2, max_fields=3, max_part_size=64 * 1024
        )
        del raw_body
        metadata_raw, upload = _parse_parts(form)
        content_type = (upload.content_type or "").split(";", 1)[0].strip().lower()
        if content_type not in {"audio/webm", "audio/mp4"}:
            return _error(415, "Only WebM and MP4 audio are accepted", "unsupported_audio_type")
        data = await upload.read(MAX_AUDIO_BYTES + 1)
        if len(data) > MAX_AUDIO_BYTES:
            return _error(413, "Audio upload exceeds the allowed size", "audio_too_large")
        if not data or not is_supported_audio(data, content_type):
            return _error(422, "Audio file is empty or invalid", "invalid_audio")
        body, duration_ms = _parse_metadata(metadata_raw)
        audio = DiaryAudioCreateIn(
            storage_key="diary/pending",
            sha256=hashlib.sha256(data).hexdigest(),
            byte_size=len(data),
            mime_type=content_type,
            duration_ms=duration_ms,
        )
    except (MultiPartException, StarletteHTTPException, ValidationError, TypeError, ValueError):
        return _error(422, "Invalid multipart voice command", "invalid_request")
    finally:
        if form is not None:
            await form.close()

    storage: S3Storage | None = getattr(request.app.state, "storage", None)
    if storage is None:
        return _error(503, "Private audio storage is unavailable", "storage_unavailable")
    try:
        receipt, created = await voice.accept_voice(
            session,
            request.app.state.sessionmaker,
            storage,
            actor,
            body,
            audio,
            data,
            timezone=settings.timezone,
        )
        response.status_code = 201 if created else 200
        response.headers["Cache-Control"] = "no-store"
        return receipt
    except StorageError as error:
        log.warning("diary.voice_storage_unavailable", error_type=type(error).__name__)
        return _error(503, "Private audio storage is unavailable", "storage_unavailable")
    except voice.VoiceCommitUnknownError:
        log.warning("diary.voice_commit_unknown")
        return _error(503, "Diary command status is temporarily unavailable", "diary_unavailable")
    except SQLAlchemyError as error:
        await session.rollback()
        log.error("diary.voice_database_error", error_type=type(error).__name__)
        return _error(503, "Diary is temporarily unavailable", "diary_unavailable")


@router.post(
    "/diary/entries/{entry_id}/transcription/retry",
    response_model=DiaryTranscriptionRetryOut,
    status_code=202,
    responses={200: {"model": DiaryTranscriptionRetryOut, "description": "Idempotent replay"}},
)
async def retry_voice_transcription(
    entry_id: UUID,
    body: DiaryTranscriptionRetryIn,
    response: Response,
    actor: CurrentUser,
    session: DbSession,
) -> DiaryTranscriptionRetryOut:
    """Queue an explicit retry for a terminally failed recording."""
    receipt, created = await service.retry_transcription(session, actor, entry_id, body)
    await session.commit()
    response.status_code = 202 if created else 200
    response.headers["Cache-Control"] = "no-store"
    return receipt
