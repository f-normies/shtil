"""GET /api/v1/health — состояние зависимостей стенда. По нему деплой решает об откате.

Узлы инференса показываются по ролям (`inference`), но на статус не влияют: выключенный Мак
не должен откатывать деплой (спека фундамента 6.5, спека #18 раздел 6). Адресов в ответе нет —
эндпоинт открыт наружу.

Модули добавляют свои разделы через `app.state.health_sections`: имя раздела → функция
от фабрики сессий. `core` модули не импортирует, словарь собирает `main.py`. Разделы только
для сведения: ошибка, таймаут или недоступная БД дают `null`, статус от них не зависит
(спека #109, 4.3).
"""

import asyncio
from collections.abc import Awaitable, Callable, Mapping

import structlog
from fastapi import APIRouter, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.core.ai.nodes import probe_nodes
from clin_compass.core.ai.runs import describe_error
from clin_compass.core.db import database_is_reachable

HealthSection = Callable[[async_sessionmaker[AsyncSession]], Awaitable[object]]

router = APIRouter(tags=["health"])
log = structlog.get_logger()


@router.get("/health")
async def health(request: Request) -> JSONResponse:
    settings = request.app.state.settings
    sections: Mapping[str, HealthSection] = request.app.state.health_sections
    database_ok, inference, *results = await asyncio.gather(
        database_is_reachable(request.app.state.engine, settings.health_timeout_seconds),
        probe_nodes(settings),
        *(
            _section(section, request.app.state.sessionmaker, settings.health_timeout_seconds)
            for section in sections.values()
        ),
    )
    content: dict[str, object] = {}
    for name, result in zip(sections, results, strict=True):
        if isinstance(result, Exception):
            # Без БД раздел падает всегда, а причину уже показывает `checks.database`.
            if database_ok:
                log.warning("health.section_failed", section=name, error=describe_error(result))
            content[name] = None
        else:
            content[name] = result
    checks = {"database": "ok" if database_ok else "error"}
    status_ok = all(value == "ok" for value in checks.values())
    status = "ok" if status_ok else "error"
    return JSONResponse(
        status_code=200 if status_ok else 503,
        content={"status": status, "checks": checks, "inference": inference, **content},
    )


async def _section(
    section: HealthSection, sessionmaker: async_sessionmaker[AsyncSession], limit_seconds: float
) -> object:
    """Ответ раздела, готовый к JSON, или его ошибка: один раздел не роняет health."""
    try:
        async with asyncio.timeout(limit_seconds):
            return jsonable_encoder(await section(sessionmaker))
    # Раздел — код модуля: любая его ошибка даёт null, а не 500 у всего health.
    except Exception as exc:  # noqa: BLE001
        return exc
