"""HTTP API текстовых сообщений, истории, прочтения и сводки непрочитанного."""

from typing import Annotated, Any
from uuid import UUID

import structlog
from fastapi import APIRouter, Query, Response
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from clin_compass.core.deps import DbSession
from clin_compass.core.errors import (
    PROBLEM_JSON,
    ProblemOut,
    ValidationProblemOut,
    problem,
)
from clin_compass.modules.identity.schemas import UserOut
from clin_compass.modules.messaging import service
from clin_compass.modules.messaging.deps import MessagingUser
from clin_compass.modules.messaging.schemas import (
    SEQ_MAX,
    MessageCreateIn,
    MessageHistoryOut,
    MessageOut,
    MessagingStatusOut,
    ReadCreateIn,
    ReadOut,
    UnreadOut,
)
from clin_compass.modules.messaging.voice_api import router as voice_router
from clin_compass.modules.messaging.voice_playback import router as playback_router

router = APIRouter(tags=["messaging"])
router.include_router(voice_router)
router.include_router(playback_router)
log = structlog.get_logger()

_PROBLEM_CONTENT: dict[str, Any] = {PROBLEM_JSON: {"schema": ProblemOut.model_json_schema()}}
_VALIDATION_PROBLEM_CONTENT: dict[str, Any] = {
    PROBLEM_JSON: {
        "schema": {
            "anyOf": [
                ProblemOut.model_json_schema(),
                ValidationProblemOut.model_json_schema(),
            ]
        }
    }
}
_AUTH_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"description": "Authentication is required", "content": _PROBLEM_CONTENT},
    403: {"description": "The authenticated role is not allowed", "content": _PROBLEM_CONTENT},
}


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


def _as_problem(error: service.MessagingError) -> JSONResponse:
    response = problem(error.status, error.detail, code=error.code)
    _no_store(response)
    return response


async def _audit_error(
    session: DbSession,
    actor: UserOut,
    *,
    patient_id: UUID | None,
    action: str,
    error: SQLAlchemyError,
) -> None:
    await session.rollback()
    log.error("messaging.database_error", error_type=type(error).__name__)
    await service.audit_denied(session, actor_id=actor.id, patient_id=patient_id, action=action)


def _unavailable() -> JSONResponse:
    response = problem(503, "Messaging is temporarily unavailable", code="messaging_unavailable")
    _no_store(response)
    return response


@router.get(
    "/patients/{patient_id}/messages",
    response_model=MessageHistoryOut,
    responses={
        **_AUTH_RESPONSES,
        404: {"description": "Conversation is not available", "content": _PROBLEM_CONTENT},
        409: {"description": "The current doctor changed", "content": _PROBLEM_CONTENT},
        422: {
            "description": "Invalid history cursor or request parameters",
            "content": _VALIDATION_PROBLEM_CONTENT,
        },
        503: {"description": "Messaging storage is unavailable", "content": _PROBLEM_CONTENT},
    },
)
async def get_history(
    patient_id: UUID,
    user: MessagingUser,
    session: DbSession,
    response: Response,
    before_seq: Annotated[int | None, Query(ge=0, le=SEQ_MAX)] = None,
    after_seq: Annotated[int | None, Query(ge=0, le=SEQ_MAX)] = None,
    expected_doctor_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> MessageHistoryOut | JSONResponse:
    """История текущей пары; чтение не двигает курсор прочтения."""
    try:
        result = await service.list_history(
            session,
            user,
            patient_id,
            before_seq=before_seq,
            after_seq=after_seq,
            expected_doctor_id=expected_doctor_id,
            limit=limit,
        )
        await session.commit()
        _no_store(response)
        return result
    except service.MessagingError as error:
        await service.audit_denied(
            session, actor_id=user.id, patient_id=patient_id, action="messaging.history"
        )
        return _as_problem(error)
    except SQLAlchemyError as error:
        await _audit_error(
            session, user, patient_id=patient_id, action="messaging.history", error=error
        )
        return _unavailable()


@router.post(
    "/patients/{patient_id}/messages",
    response_model=MessageOut,
    status_code=201,
    responses={
        200: {"model": MessageOut, "description": "Idempotent replay"},
        **_AUTH_RESPONSES,
        404: {"description": "Conversation is not available", "content": _PROBLEM_CONTENT},
        409: {
            "description": "The pair changed or the idempotency key conflicts",
            "content": _PROBLEM_CONTENT,
        },
        422: {
            "description": "Invalid message or request body",
            "content": _VALIDATION_PROBLEM_CONTENT,
        },
        503: {"description": "Messaging storage is unavailable", "content": _PROBLEM_CONTENT},
    },
)
async def post_text(
    patient_id: UUID,
    body: MessageCreateIn,
    user: MessagingUser,
    session: DbSession,
    response: Response,
) -> MessageOut | JSONResponse:
    """Принять сообщение ровно один раз; отправителем остаётся вошедший пользователь."""
    try:
        message, created = await service.send_text(session, user, patient_id, body)
        await session.commit()
        response.status_code = 201 if created else 200
        _no_store(response)
        return message
    except service.MessagingError as error:
        await service.audit_denied(
            session, actor_id=user.id, patient_id=patient_id, action="messaging.send"
        )
        return _as_problem(error)
    except SQLAlchemyError as error:
        await _audit_error(
            session, user, patient_id=patient_id, action="messaging.send", error=error
        )
        return _unavailable()


@router.post(
    "/patients/{patient_id}/messages/read",
    response_model=ReadOut,
    responses={
        **_AUTH_RESPONSES,
        404: {"description": "Conversation is not available", "content": _PROBLEM_CONTENT},
        409: {"description": "The current doctor changed", "content": _PROBLEM_CONTENT},
        422: {
            "description": "Read cursor is outside the conversation",
            "content": _VALIDATION_PROBLEM_CONTENT,
        },
        503: {"description": "Messaging storage is unavailable", "content": _PROBLEM_CONTENT},
    },
)
async def post_read(
    patient_id: UUID,
    body: ReadCreateIn,
    user: MessagingUser,
    session: DbSession,
    response: Response,
) -> ReadOut | JSONResponse:
    """Подтвердить прочтение до номера сообщения с монотонным курсором."""
    try:
        result = await service.mark_read(session, user, patient_id, body)
        await session.commit()
        _no_store(response)
        return result
    except service.MessagingError as error:
        await service.audit_denied(
            session, actor_id=user.id, patient_id=patient_id, action="messaging.read"
        )
        return _as_problem(error)
    except SQLAlchemyError as error:
        await _audit_error(
            session, user, patient_id=patient_id, action="messaging.read", error=error
        )
        return _unavailable()


@router.get(
    "/messaging/unread",
    response_model=list[UnreadOut],
    responses={
        **_AUTH_RESPONSES,
        403: {"description": "The authenticated role is not allowed", "content": _PROBLEM_CONTENT},
        503: {"description": "Messaging storage is unavailable", "content": _PROBLEM_CONTENT},
    },
)
async def get_unread(
    user: MessagingUser, session: DbSession, response: Response
) -> list[UnreadOut] | JSONResponse:
    """Непрочитанные текущих пар без текста сообщений."""
    try:
        result = await service.list_unread(session, user)
        await session.commit()
        _no_store(response)
        return result
    except service.MessagingError as error:
        await service.audit_denied(
            session, actor_id=user.id, patient_id=None, action="messaging.unread"
        )
        return _as_problem(error)
    except SQLAlchemyError as error:
        await _audit_error(session, user, patient_id=None, action="messaging.unread", error=error)
        return _unavailable()


@router.get(
    "/patients/{patient_id}/messaging-status",
    response_model=MessagingStatusOut,
    responses={
        **_AUTH_RESPONSES,
        404: {"description": "Conversation is not available", "content": _PROBLEM_CONTENT},
        503: {"description": "Messaging storage is unavailable", "content": _PROBLEM_CONTENT},
    },
)
async def get_messaging_status(
    patient_id: UUID,
    user: MessagingUser,
    session: DbSession,
    response: Response,
) -> MessagingStatusOut | JSONResponse:
    """Return the current peer's presence without caching or exposing other pairs."""
    try:
        result = await service.messaging_status(session, user, patient_id)
        await session.commit()
        _no_store(response)
        return result
    except service.MessagingError as error:
        await service.audit_denied(
            session, actor_id=user.id, patient_id=patient_id, action="messaging.status"
        )
        return _as_problem(error)
    except SQLAlchemyError as error:
        await _audit_error(
            session, user, patient_id=patient_id, action="messaging.status", error=error
        )
        return _unavailable()
