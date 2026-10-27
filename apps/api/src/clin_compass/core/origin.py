"""Проверка источника запроса: защита от CSRF и подключения WebSocket с чужих страниц.

Браузер сообщает источник заголовками Sec-Fetch-Site и Origin. Запрос с другого сайта
или с соседнего поддомена (`same-site`) с cookie нашего пользователя отклоняем:
SameSite=Lax от поддоменов того же домена не защищает. Клиент без этих заголовков —
не браузер, чужую cookie он подставить не может.
"""

from collections.abc import Mapping
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from clin_compass.core.errors import problem

ALLOWED_FETCH_SITES = {"same-origin", "none"}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def is_same_origin(headers: Mapping[str, str]) -> bool:
    fetch_site = headers.get("sec-fetch-site")
    if fetch_site is not None and fetch_site not in ALLOWED_FETCH_SITES:
        return False
    origin = headers.get("origin")
    if origin is None:
        return True
    return urlparse(origin).netloc == headers.get("host")


def install_origin_guard(app: FastAPI) -> None:
    """Небезопасные методы (POST, PUT, PATCH, DELETE) — только со своего origin."""

    @app.middleware("http")
    async def origin_guard(request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.method not in SAFE_METHODS and not is_same_origin(request.headers):
            return problem(403, "Cross-origin request rejected")
        return await call_next(request)
