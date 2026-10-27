"""Ошибки API в формате RFC 9457 (application/problem+json)."""

from http import HTTPStatus
from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from starlette.exceptions import HTTPException

PROBLEM_JSON = "application/problem+json"

log = structlog.get_logger()


class ProblemOut(BaseModel):
    """Тело ошибки RFC 9457; `code`, `current_version` и `retry_after` — расширения для клиента."""

    model_config = ConfigDict(extra="forbid")

    type: str
    title: str
    status: int
    detail: str | None = None
    code: str | None = None
    current_version: int | None = None
    # Секунды до повтора после 429; то же значение — в заголовке Retry-After.
    retry_after: int | None = None


class ValidationProblemOut(ProblemOut):
    """Структурная ошибка запроса с деталями проверки полей."""

    errors: list[dict[str, Any]]


class RateLimitedError(Exception):
    """Лимит частоты исчерпан (спека #23): ответ 429 через `retry_after` секунд.

    `limits` — какие лимиты сработали (`login.pair`, `invite.create`…), только для лога.
    """

    def __init__(self, retry_after: int, *limits: str) -> None:
        super().__init__(f"retry after {retry_after} s")
        self.retry_after = max(1, retry_after)
        self.limits = limits


# Описание 429 для OpenAPI у ручек с лимитом частоты.
RATE_LIMITED_RESPONSE: dict[int | str, dict[str, Any]] = {
    429: {
        "description": "Too many attempts, retry after `retry_after` seconds",
        "content": {PROBLEM_JSON: {"schema": ProblemOut.model_json_schema()}},
    }
}


def problem(status: int, detail: str | None = None, **extra: object) -> JSONResponse:
    body: dict[str, object] = {
        "type": "about:blank",
        "title": HTTPStatus(status).phrase,
        "status": status,
    }
    if detail is not None:
        body["detail"] = detail
    body.update(extra)
    return JSONResponse(status_code=status, content=body, media_type=PROBLEM_JSON)


async def _http_exception(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, HTTPException)
    detail = exc.detail if isinstance(exc.detail, str) else None
    response = problem(exc.status_code, detail)
    if exc.headers:
        response.headers.update(exc.headers)
    return response


async def _rate_limited(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RateLimitedError)
    # Событие безопасности. Адрес почты и IP в лог не пишем.
    log.info(
        "http.rate_limited",
        path=request.url.path,
        limits=exc.limits,
        retry_after=exc.retry_after,
    )
    response = problem(429, "Too many attempts", code="rate_limited", retry_after=exc.retry_after)
    response.headers["Retry-After"] = str(exc.retry_after)
    return response


async def _validation_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    errors = [
        {"loc": list(error.get("loc", ())), "msg": error.get("msg", "")} for error in exc.errors()
    ]
    return problem(422, "Request validation failed", errors=errors)


async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
    # Подробности — только в лог: в тексте исключения могут быть данные пациента.
    log.error("http.unhandled_error", path=request.url.path, exc_info=exc)
    return problem(500)


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(HTTPException, _http_exception)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(RateLimitedError, _rate_limited)
    app.add_exception_handler(Exception, _unhandled)
