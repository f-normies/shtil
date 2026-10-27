"""HTTP upload для закрытых голосовых сообщений."""

from collections import Counter
from typing import Any
from uuid import UUID

import structlog
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.datastructures import FormData, UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.formparsers import MultiPartException
from starlette.requests import Request as StarletteRequest

from clin_compass.core.deps import DbSession
from clin_compass.core.errors import PROBLEM_JSON, ProblemOut, ValidationProblemOut, problem
from clin_compass.core.storage import S3Storage, StorageError
from clin_compass.modules.messaging import service, voice
from clin_compass.modules.messaging.deps import MessagingUser
from clin_compass.modules.messaging.schemas import MessageOut

MAX_AUDIO_BYTES = 25 * 1024 * 1024
MAX_MULTIPART_BYTES = 26 * 1024 * 1024
router = APIRouter(tags=["messaging"])
log = structlog.get_logger()

_AUTH_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {
        "description": "Authentication is required",
        "content": {PROBLEM_JSON: {"schema": ProblemOut.model_json_schema()}},
    },
    403: {
        "description": "The authenticated role is not allowed",
        "content": {PROBLEM_JSON: {"schema": ProblemOut.model_json_schema()}},
    },
}
_VALIDATION_CONTENT = {
    PROBLEM_JSON: {
        "schema": {
            "anyOf": [ProblemOut.model_json_schema(), ValidationProblemOut.model_json_schema()]
        }
    }
}
_MULTIPART_SCHEMA = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": ["expected_doctor_id", "client_message_id", "file"],
                    "properties": {
                        "expected_doctor_id": {"type": "string", "format": "uuid"},
                        "client_message_id": {"type": "string", "format": "uuid"},
                        "file": {"type": "string", "format": "binary"},
                    },
                    "additionalProperties": False,
                }
            }
        },
    }
}


def _error(status: int, detail: str, code: str) -> JSONResponse:
    response = problem(status, detail, code=code)
    response.headers["Cache-Control"] = "no-store"
    return response


async def _read_capped_body(request: Request) -> bytes:
    declared_length = request.headers.get("content-length")
    if declared_length is not None:
        try:
            if int(declared_length) > MAX_MULTIPART_BYTES:
                raise ValueError("body too large")
        except ValueError as error:
            if str(error) == "body too large":
                raise
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_MULTIPART_BYTES:
            raise ValueError("body too large")
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


def _parse_uuid(value: object) -> UUID:
    if not isinstance(value, str):
        raise ValueError("invalid multipart field")
    return UUID(value)


def _valid_parts(form: FormData) -> tuple[UUID, UUID, UploadFile]:
    parts = form.multi_items()
    names = Counter(name for name, _ in parts)
    if len(parts) != 3 or names != Counter(
        {"expected_doctor_id": 1, "client_message_id": 1, "file": 1}
    ):
        raise ValueError("invalid multipart fields")
    values = dict(parts)
    upload = values["file"]
    if not isinstance(upload, UploadFile):
        raise ValueError("invalid multipart file")
    return (
        _parse_uuid(values["expected_doctor_id"]),
        _parse_uuid(values["client_message_id"]),
        upload,
    )


def _normalise_mime(upload: UploadFile) -> str | None:
    raw = upload.content_type
    if raw is None:
        return None
    return raw.split(";", 1)[0].strip().lower()


@router.post(
    "/patients/{patient_id}/messages/voice",
    response_model=MessageOut,
    status_code=201,
    responses={
        200: {"model": MessageOut, "description": "Idempotent replay"},
        **_AUTH_RESPONSES,
        404: {
            "description": "Conversation is not available",
            "content": {PROBLEM_JSON: {"schema": ProblemOut.model_json_schema()}},
        },
        409: {
            "description": "The pair changed or the idempotency key conflicts",
            "content": {PROBLEM_JSON: {"schema": ProblemOut.model_json_schema()}},
        },
        413: {
            "description": "Audio or multipart body exceeds its limit",
            "content": {PROBLEM_JSON: {"schema": ProblemOut.model_json_schema()}},
        },
        415: {
            "description": "Unsupported audio MIME type",
            "content": {PROBLEM_JSON: {"schema": ProblemOut.model_json_schema()}},
        },
        422: {"description": "Invalid multipart fields", "content": _VALIDATION_CONTENT},
        503: {
            "description": "Private audio storage or messaging is unavailable",
            "content": {PROBLEM_JSON: {"schema": ProblemOut.model_json_schema()}},
        },
    },
    openapi_extra=_MULTIPART_SCHEMA,
)
async def post_voice(
    patient_id: UUID,
    request: Request,
    user: MessagingUser,
    session: DbSession,
    response: Response,
) -> MessageOut | JSONResponse:
    """Принять один ограниченный аудиофайл до передачи текста пациентского голоса в ASR."""
    try:
        await service._lock_current_pair(session, user, patient_id, expected_doctor_id=None)
        # The preflight only hides validation and storage details from non-members.
        # Release the patient row lock before parsing or reading the uploaded file.
        await session.rollback()
    except service.MessagingError as error:
        await service.audit_denied(
            session, actor_id=user.id, patient_id=patient_id, action="messaging.send"
        )
        return _error(error.status, error.detail, error.code)
    except SQLAlchemyError as error:
        await session.rollback()
        log.error("messaging.database_error", error_type=type(error).__name__)
        await service.audit_denied(
            session, actor_id=user.id, patient_id=patient_id, action="messaging.send"
        )
        return _error(503, "Messaging is temporarily unavailable", "messaging_unavailable")

    try:
        raw_body = await _read_capped_body(request)
    except ValueError:
        return _error(413, "Audio upload exceeds the allowed size", "audio_too_large")

    form: FormData | None = None
    try:
        form = await _replay_request(request, raw_body).form(
            max_files=2,
            max_fields=4,
            max_part_size=64 * 1024,
        )
        # Файл уже во временном файле парсера: тело до 26 МиБ не держим рядом с аудио.
        del raw_body
        expected_doctor_id, client_message_id, upload = _valid_parts(form)
        content_type = _normalise_mime(upload)
        if content_type not in {"audio/webm", "audio/mp4"}:
            return _error(415, "Only WebM and MP4 audio are accepted", "unsupported_audio_type")
        data = await upload.read(MAX_AUDIO_BYTES + 1)
        if len(data) > MAX_AUDIO_BYTES:
            return _error(413, "Audio upload exceeds the allowed size", "audio_too_large")
        if not data:
            return _error(422, "Audio file must not be empty", "invalid_request")
    except (MultiPartException, StarletteHTTPException, ValueError):
        return _error(422, "Invalid multipart request", "invalid_request")
    finally:
        if form is not None:
            await form.close()

    storage: S3Storage | None = getattr(request.app.state, "storage", None)
    if storage is None:
        return _error(503, "Private audio storage is unavailable", "storage_unavailable")
    try:
        message, created = await voice.accept_voice(
            session,
            request.app.state.sessionmaker,
            storage,
            user,
            patient_id=patient_id,
            expected_doctor_id=expected_doctor_id,
            client_message_id=client_message_id,
            data=data,
            content_type=content_type,
        )
        response.status_code = 201 if created else 200
        response.headers["Cache-Control"] = "no-store"
        return message
    except service.MessagingError as error:
        await service.audit_denied(
            session, actor_id=user.id, patient_id=patient_id, action="messaging.send"
        )
        return _error(error.status, error.detail, error.code)
    except StorageError as error:
        log.warning("messaging.storage_unavailable", error_type=type(error).__name__)
        return _error(503, "Private audio storage is unavailable", "storage_unavailable")
    except voice.VoiceCommitUnknownError:
        log.warning("messaging.voice_commit_unknown")
        return _error(503, "Messaging is temporarily unavailable", "messaging_unavailable")
    except SQLAlchemyError as error:
        await session.rollback()
        log.error("messaging.database_error", error_type=type(error).__name__)
        await service.audit_denied(
            session, actor_id=user.id, patient_id=patient_id, action="messaging.send"
        )
        return _error(503, "Messaging is temporarily unavailable", "messaging_unavailable")
