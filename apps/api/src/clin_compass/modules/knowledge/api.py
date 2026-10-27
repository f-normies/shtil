"""HTTP API базы знаний для врача: справочный вопрос и фрагмент по id (спека #27, раздел 8),
источник для инспектора (спека #106)."""

from typing import Annotated, Any
from uuid import UUID

import structlog
from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.core.ai.nodes import (
    AiNoAnswerError,
    AiOutputError,
    AiServiceError,
    NodesUnavailableError,
)
from clin_compass.core.ai.runs import describe_error
from clin_compass.core.deps import DbSession
from clin_compass.core.errors import PROBLEM_JSON, ProblemOut, ValidationProblemOut, problem
from clin_compass.modules.identity.deps import DoctorUser
from clin_compass.modules.knowledge import service
from clin_compass.modules.knowledge.deps import KnowledgeAiDep, KnowledgeSourcesDep
from clin_compass.modules.knowledge.schemas import Answer, AnswerIn, Fragment, SourceView

router = APIRouter(prefix="/knowledge", tags=["knowledge"])

log = structlog.get_logger()

UNAVAILABLE = "Модель недоступна, повторите позже"
FAILED = "Модель не дала ответа, повторите позже"
# Самый длинный `locator` в knowledge/ — около 200 знаков.
LOCATOR_MAX_LENGTH = 500

_PROBLEM_CONTENT: dict[str, Any] = {PROBLEM_JSON: {"schema": ProblemOut.model_json_schema()}}
_AUTH_PROBLEM_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"description": "Authentication is required", "content": _PROBLEM_CONTENT},
    403: {"description": "Only doctors use the knowledge base", "content": _PROBLEM_CONTENT},
}
_VALIDATION_RESPONSE: dict[str, Any] = {
    "description": "Request validation problem",
    "content": {PROBLEM_JSON: {"schema": ValidationProblemOut.model_json_schema()}},
}


@router.post(
    "/answers",
    response_model=Answer,
    responses={
        **_AUTH_PROBLEM_RESPONSES,
        422: _VALIDATION_RESPONSE,
        502: {"description": "Model failed to answer", "content": _PROBLEM_CONTENT},
        503: {"description": "Model nodes are unavailable", "content": _PROBLEM_CONTENT},
    },
)
async def create_answer(
    body: AnswerIn, request: Request, _doctor: DoctorUser, session: DbSession, ai: KnowledgeAiDep
) -> Answer | JSONResponse:
    """Ответ на вопрос врача по клиническим рекомендациям и инструкциям с проверенными цитатами."""
    if ai is None:
        return problem(503, UNAVAILABLE, code="model_unavailable")
    # Сессия запроса нужна была только для проверки входа (она только читает). Закрываем её,
    # чтобы соединение не висело в открытой транзакции, пока отвечает модель. Поиск откроет
    # свою короткую сессию.
    await session.close()
    sessionmaker: async_sessionmaker[AsyncSession] = request.app.state.sessionmaker
    try:
        return await service.answer(sessionmaker, ai, body.question, source_ids=body.source_ids)
    except NodesUnavailableError as exc:
        log.warning("knowledge.answer_unavailable", error=describe_error(exc))
        return problem(503, UNAVAILABLE, code="model_unavailable")
    except (AiServiceError, AiNoAnswerError, AiOutputError) as exc:
        # Текст ошибки не пишем: в нём может быть вопрос с данными пациента.
        log.warning("knowledge.answer_failed", error=describe_error(exc))
        return problem(502, FAILED, code="model_failed")


@router.get(
    "/fragments/{fragment_id}",
    response_model=Fragment,
    responses={
        **_AUTH_PROBLEM_RESPONSES,
        404: {"description": "Fragment not found", "content": _PROBLEM_CONTENT},
    },
)
async def get_fragment(
    fragment_id: UUID, _doctor: DoctorUser, session: DbSession
) -> Fragment | JSONResponse:
    """Фрагмент с документом и разделом: по нему интерфейс открывает цитату."""
    fragment = await service.get_fragment(session, fragment_id)
    if fragment is None:
        return problem(404, "Fragment not found")
    return fragment


@router.get(
    "/sources/{source_id}",
    response_model=SourceView,
    responses={
        **_AUTH_PROBLEM_RESPONSES,
        404: {"description": "Source or fragment not found", "content": _PROBLEM_CONTENT},
        422: _VALIDATION_RESPONSE,
    },
)
async def view_source(
    source_id: str,
    _doctor: DoctorUser,
    session: DbSession,
    sources: KnowledgeSourcesDep,
    locator: Annotated[str | None, Query(min_length=1, max_length=LOCATOR_MAX_LENGTH)] = None,
    fragment_id: UUID | None = None,
) -> SourceView | JSONResponse:
    """Документ из манифеста и текст: разделы по `locator` из данных или фрагмент утверждения.

    Нужен ровно один из параметров. Текста нет в индексе — 200 с `found: false`.
    """
    if (locator is None) == (fragment_id is None):
        return problem(
            422,
            "Request validation failed",
            errors=[{"loc": ["query"], "msg": "Pass exactly one of locator and fragment_id"}],
        )
    if source_id not in sources.sources:
        return problem(404, "Source not found")
    view = await service.view_source(
        session, sources, source_id, locator=locator, fragment_id=fragment_id
    )
    if view is None:
        return problem(404, "Fragment not found")
    return view
