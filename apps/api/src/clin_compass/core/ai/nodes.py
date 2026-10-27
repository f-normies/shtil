"""Узлы инференса: роли, ошибки, правило перехода к следующему адресу, проверка для health.

У каждой роли список адресов в настройках (спека #18, раздел 4). Клиент идёт по нему по порядку
и переходит к следующему адресу, только если узел не принял запрос (`is_unavailable`).
"""

import asyncio
from collections.abc import Mapping
from typing import Literal, cast, get_args

import httpx2
import structlog
from openai import AsyncOpenAI, omit
from pydantic_ai.exceptions import ModelHTTPError

from clin_compass.core.config import Settings

AiRole = Literal["llm", "embeddings", "asr", "docparse", "rerank"]
NodeStatus = Literal["ok", "error"]
ROLES: tuple[AiRole, ...] = get_args(AiRole)

log = structlog.get_logger()

# Узел не готов (llama-server грузит модель) или за прокси нет сервера.
_UNAVAILABLE_STATUSES = frozenset({502, 503, 504})
# Узлы работают без ключа. Явная заглушка не даёт SDK взять OPENAI_API_KEY из окружения.
_NO_API_KEY = "not-used"


class AiError(RuntimeError):
    """Общий предок ошибок клиентов core/ai. Текст ошибки не содержит ни входа, ни ответа модели."""


class AiNotConfiguredError(AiError):
    """У роли нет ни одного адреса в настройках."""

    def __init__(self, role: AiRole) -> None:
        super().__init__(f"Не заданы адреса узлов роли {role}: COMPASS_{role.upper()}_URLS.")
        self.role = role


class NodesUnavailableError(AiError):
    """Ни один узел роли не принял запрос. Задача worker'а повторится позже."""

    def __init__(self, role: AiRole) -> None:
        super().__init__(f"Все узлы роли {role} недоступны.")
        self.role = role


class AiServiceError(AiError):
    """Узел ответил ошибкой, на которую переходить к другому узлу бессмысленно."""

    def __init__(self, role: AiRole, status: int) -> None:
        super().__init__(f"Узел роли {role} ответил кодом {status}.")
        self.role = role
        self.status = status


class AiNoAnswerError(AiError):
    """Узел принял запрос, но ответа нет: истёк таймаут ответа или оборвалось соединение.

    К другому узлу запрос не уходит: он мог дойти до модели, и повтор удвоил бы нагрузку.
    Задача worker'а повторится позже.
    """

    def __init__(self, role: AiRole) -> None:
        super().__init__(f"Узел роли {role} принял запрос, но не ответил.")
        self.role = role


class AiOutputError(AiError):
    """Модель ответила не по схеме.

    Ответ и ошибки его разбора в исключение не попадают: в них значения из медицинского текста.
    """

    def __init__(self, role: AiRole) -> None:
        super().__init__(f"Узел роли {role}: модель ответила не по схеме.")
        self.role = role


def openai_client(url: str, timeout: httpx2.Timeout) -> AsyncOpenAI:
    """OpenAI-клиент узла: без повторов, без ключей и заголовков из окружения.

    SDK сам читает `OPENAI_API_KEY`, `OPENAI_ORG_ID`, `OPENAI_PROJECT_ID` и
    `OPENAI_CUSTOM_HEADERS` и отправил бы их на Мак. Явный `Authorization` вытесняет ключ
    и одноимённый заголовок из `OPENAI_CUSTOM_HEADERS`. `omit` убирает заголовки организации
    и проекта: пустая строка в `organization` всё равно ушла бы пустым заголовком. Прочие
    заголовки из `OPENAI_CUSTOM_HEADERS` SDK отправит: на стенде эту переменную не задаём.
    Повторы 5xx (`max_retries`) выключены: повторяет очередь.
    """
    headers = {
        "Authorization": f"Bearer {_NO_API_KEY}",
        "OpenAI-Organization": omit,
        "OpenAI-Project": omit,
    }
    return AsyncOpenAI(
        base_url=f"{url}/v1",
        api_key=_NO_API_KEY,
        organization="",
        project="",
        max_retries=0,
        timeout=timeout,
        # SDK принимает omit в заголовках, но объявляет тип Mapping[str, str].
        default_headers=cast(Mapping[str, str], headers),
    )


def node_urls(settings: Settings, role: AiRole) -> tuple[str, ...]:
    """Адреса узлов роли из настроек.

    Raises:
        AiNotConfiguredError: У роли нет адресов.
    """
    urls: tuple[str, ...] = getattr(settings, f"{role}_urls")
    if not urls:
        raise AiNotConfiguredError(role)
    return urls


def is_unavailable(exc: BaseException) -> bool:
    """Узел не принял запрос, и его можно отправить на следующий адрес.

    Да — отказ или таймаут соединения, ответ 502, 503, 504. Нет — всё остальное: таймаут
    ответа (запрос мог дойти до модели) и ошибки, на которые другой узел ответит так же.
    OpenAI SDK и PydanticAI заворачивают сетевую ошибку httpx2 в свои, поэтому смотрим всю
    цепочку `__cause__`.
    """
    current: BaseException | None = exc
    while current is not None:
        if isinstance(current, httpx2.ConnectError | httpx2.ConnectTimeout):
            return True
        if isinstance(current, ModelHTTPError):
            return current.status_code in _UNAVAILABLE_STATUSES
        if isinstance(current, httpx2.HTTPStatusError):
            return current.response.status_code in _UNAVAILABLE_STATUSES
        current = current.__cause__
    return False


def report_unavailable(role: AiRole, node: str | None, exc: BaseException) -> None:
    """Пишет в лог переход к следующему узлу: роль, адрес и класс ошибки, без запроса."""
    log.warning("ai.node_unavailable", role=role, node=node, error=type(exc).__name__)


async def probe_nodes(settings: Settings) -> dict[AiRole, list[NodeStatus]]:
    """Опрашивает `GET <узел>/health` у всех узлов всех ролей параллельно.

    `health_timeout_seconds` ограничивает каждую фазу запроса и весь опрос узла целиком:
    узел, который отдаёт ответ по байту, тоже получает `error`.

    Returns:
        По каждой роли статусы узлов в порядке настроек. Пустой список — роль не настроена.
    """
    deadline = settings.health_timeout_seconds
    async with httpx2.AsyncClient(timeout=deadline) as client:

        async def probe(url: str) -> NodeStatus:
            try:
                async with asyncio.timeout(deadline):
                    response = await client.get(f"{url}/health")
            except (httpx2.HTTPError, TimeoutError):
                return "error"
            return "ok" if response.status_code == 200 else "error"

        by_role = {role: getattr(settings, f"{role}_urls") for role in ROLES}
        results = await asyncio.gather(*(probe(url) for urls in by_role.values() for url in urls))

    statuses: dict[AiRole, list[NodeStatus]] = {}
    position = 0
    for role, urls in by_role.items():
        statuses[role] = list(results[position : position + len(urls)])
        position += len(urls)
    return statuses


__all__ = [
    "ROLES",
    "AiError",
    "AiNoAnswerError",
    "AiNotConfiguredError",
    "AiOutputError",
    "AiRole",
    "AiServiceError",
    "NodeStatus",
    "NodesUnavailableError",
    "is_unavailable",
    "node_urls",
    "openai_client",
    "probe_nodes",
    "report_unavailable",
]
