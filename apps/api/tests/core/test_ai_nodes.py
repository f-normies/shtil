"""Узлы инференса: когда переходить к следующему адресу и как health видит узлы (спека #18)."""

import asyncio
import itertools
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx2
import openai
import pytest
from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError

from clin_compass.core.ai.asr import AsrRejectedError
from clin_compass.core.ai.nodes import (
    AiError,
    AiNoAnswerError,
    AiNotConfiguredError,
    AiOutputError,
    AiServiceError,
    NodesUnavailableError,
    is_unavailable,
    probe_nodes,
)
from clin_compass.core.config import Settings
from tests.core.fake_nodes import dead_node_url, fake_node, health_ok

REQUEST = httpx2.Request("POST", "http://100.64.0.1:8000/v1/chat/completions")


def _caused(*chain: BaseException) -> BaseException:
    """Цепочка ошибок, как её строят OpenAI SDK и PydanticAI: внешняя — первой."""
    for outer, inner in itertools.pairwise(chain):
        outer.__cause__ = inner
    return chain[0]


def _status_error(status: int) -> httpx2.HTTPStatusError:
    response = httpx2.Response(status, request=REQUEST)
    return httpx2.HTTPStatusError("ошибка узла", request=REQUEST, response=response)


@pytest.mark.parametrize(
    ("exc", "unavailable"),
    [
        # Узел не принял соединение: отказ или таймаут соединения.
        (
            _caused(
                ModelAPIError("qwen3.8-27b", "Connection error."),
                openai.APIConnectionError(request=REQUEST),
                httpx2.ConnectError("Connection refused"),
            ),
            True,
        ),
        (
            _caused(
                ModelAPIError("qwen3.8-27b", "Request timed out."),
                openai.APITimeoutError(request=REQUEST),
                httpx2.ConnectTimeout("timed out"),
            ),
            True,
        ),
        (httpx2.ConnectError("Connection refused"), True),
        # Запрос дошёл до модели: повтор на другом узле удвоил бы нагрузку.
        (
            _caused(
                ModelAPIError("qwen3.8-27b", "Request timed out."),
                openai.APITimeoutError(request=REQUEST),
                httpx2.ReadTimeout("timed out"),
            ),
            False,
        ),
        # Узел не готов или за прокси нет сервера.
        (ModelHTTPError(502, "qwen3.8-27b"), True),
        (ModelHTTPError(503, "qwen3.8-27b"), True),
        (ModelHTTPError(504, "qwen3.8-27b"), True),
        (_status_error(503), True),
        # Ошибка запроса или модели: другой узел ответит так же.
        (ModelHTTPError(400, "qwen3.8-27b"), False),
        (ModelHTTPError(422, "qwen3.8-27b"), False),
        (ModelHTTPError(500, "qwen3.8-27b"), False),
        (_status_error(500), False),
        (ValueError("не сетевая ошибка"), False),
    ],
)
def test_is_unavailable(exc: BaseException, unavailable: bool) -> None:
    assert is_unavailable(exc) is unavailable


async def test_probe_nodes_reports_each_node_in_settings_order() -> None:
    async with fake_node(health_ok) as live:
        settings = Settings(
            database_url="postgresql+asyncpg://unused/none",
            environment="test",
            llm_urls=(live.url,),
            embeddings_urls=(dead_node_url(), live.url),
            asr_urls=(),
            docparse_urls=(live.url,),
            rerank_urls=(live.url,),
        )
        statuses = await probe_nodes(settings)

    assert statuses == {
        "llm": ["ok"],
        "embeddings": ["error", "ok"],
        "asr": [],
        "docparse": ["ok"],
        "rerank": ["ok"],
    }
    assert [request.path for request in live.requests] == ["/health"] * 4


async def test_probe_nodes_marks_not_ready_node_as_error() -> None:
    # llama-server отвечает 503, пока грузит модель.
    async with fake_node(lambda request: (503, {"error": "loading model"})) as loading:
        settings = Settings(
            database_url="postgresql+asyncpg://unused/none",
            environment="test",
            llm_urls=(),
            embeddings_urls=(loading.url,),
            asr_urls=(),
        )
        statuses = await probe_nodes(settings)

    assert statuses["embeddings"] == ["error"]


def _llm_settings(url: str) -> Settings:
    return Settings(
        database_url="postgresql+asyncpg://unused/none",
        environment="test",
        health_timeout_seconds=0.2,
        llm_urls=(url,),
        embeddings_urls=(),
        asr_urls=(),
    )


async def test_probe_nodes_gives_up_on_hanging_node_after_health_timeout() -> None:
    answer = asyncio.Event()

    async def hang(request: object) -> tuple[int, object]:
        await answer.wait()
        return 200, {"status": "ok"}

    async with fake_node(hang) as hanging:
        # Внешний срок только страхует тест от зависания; узел отвечает лишь после проверки.
        async with asyncio.timeout(10):
            statuses = await probe_nodes(_llm_settings(hanging.url))
        answer.set()

    assert statuses["llm"] == ["error"]


@asynccontextmanager
async def _trickling_node() -> AsyncIterator[str]:
    """Узел отдаёт ответ /health по байту раз в 50 мс: таймаут чтения не срабатывает ни разу."""
    stop = asyncio.Event()

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.readuntil(b"\r\n\r\n")
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 1000\r\nConnection: close\r\n\r\n")
        try:
            while not stop.is_set():
                writer.write(b" ")
                await writer.drain()
                await asyncio.sleep(0.05)
        except ConnectionError:
            pass
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    async with server:
        yield f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"
        stop.set()


async def test_probe_nodes_has_overall_deadline_for_slow_answer() -> None:
    async with _trickling_node() as url:
        started = time.monotonic()
        async with asyncio.timeout(10):
            statuses = await probe_nodes(_llm_settings(url))
        elapsed = time.monotonic() - started

    assert statuses["llm"] == ["error"]
    # Весь ответ шёл бы 50 с; щедрая граница не даёт тесту падать на медленном CI.
    assert elapsed < 5


@pytest.mark.parametrize(
    "error",
    [
        AiNotConfiguredError("llm"),
        NodesUnavailableError("llm"),
        AiServiceError("llm", 500),
        AiNoAnswerError("llm"),
        AiOutputError("llm"),
        AsrRejectedError(415, "формат не поддерживается"),
    ],
)
def test_client_errors_share_one_base_class(error: Exception) -> None:
    # Вызывающий код ловит любую ошибку клиентов core/ai одним классом.
    assert isinstance(error, AiError)
