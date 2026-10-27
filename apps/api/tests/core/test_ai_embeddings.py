"""Клиент эмбеддингов: префикс инструкции получают только запросы (спека #16, раздел 4),
узлы перебираются по порядку, каждый вызов пишется в ai_runs (спека #18, раздел 5).
"""

import asyncio
from collections.abc import Sequence
from typing import Any

import pytest
import structlog
from pydantic_ai import Embedder
from pydantic_ai.embeddings import EmbeddingResult, EmbeddingSettings, TestEmbeddingModel
from pydantic_ai.embeddings.result import EmbedInputType

from clin_compass.core.ai.embeddings import InstructEmbeddingModel, build_embedder
from clin_compass.core.ai.nodes import (
    AiNoAnswerError,
    AiNotConfiguredError,
    AiServiceError,
    NodesUnavailableError,
)
from clin_compass.core.config import Settings
from tests.core.fake_nodes import (
    MemoryRecorder,
    Reply,
    Request,
    assert_no_ambient_openai_values,
    dead_node_url,
    fake_node,
    set_ambient_openai_env,
)


class SpyEmbeddingModel(TestEmbeddingModel):
    """Тестовая модель, которая запоминает тексты в том виде, в каком они ушли бы на сервер."""

    def __init__(self) -> None:
        super().__init__()
        self.sent: list[str] = []

    async def embed(
        self,
        inputs: str | Sequence[str],
        *,
        input_type: EmbedInputType,
        settings: EmbeddingSettings | None = None,
    ) -> EmbeddingResult:
        self.sent.extend([inputs] if isinstance(inputs, str) else inputs)
        return await super().embed(inputs, input_type=input_type, settings=settings)


def _llama(request: Request) -> Reply:
    """Ответ llama-server на /v1/embeddings: по вектору на текст и счётчик токенов."""
    body = request.json()
    data = [
        {"object": "embedding", "index": i, "embedding": [0.6, 0.8]}
        for i, _ in enumerate(body["input"])
    ]
    usage = {"prompt_tokens": 7, "total_tokens": 7}
    return 200, {"object": "list", "model": body["model"], "data": data, "usage": usage}


def _server_error(request: Request) -> Reply:
    return 500, {"error": {"message": "server error", "type": "server_error"}}


def _settings(*urls: str, **overrides: Any) -> Settings:
    # Значения передаём явно: Makefile экспортирует .env, где может стоять настоящий адрес.
    return Settings(
        database_url="postgresql+asyncpg://unused/none",
        environment="test",
        embeddings_urls=urls,
        **overrides,
    )


def _build(settings: Settings, recorder: MemoryRecorder | None = None) -> Embedder:
    return build_embedder(settings, recorder or MemoryRecorder(), task="knowledge.search")


def _embedder(inner: SpyEmbeddingModel) -> Embedder:
    return Embedder(InstructEmbeddingModel(inner, instruction="Найди фрагмент инструкции"))


async def test_query_is_sent_with_instruction_prefix() -> None:
    inner = SpyEmbeddingModel()

    await _embedder(inner).embed_query("Противопоказания Авиандра")

    assert inner.sent == ["Instruct: Найди фрагмент инструкции\nQuery: Противопоказания Авиандра"]


async def test_documents_are_sent_without_prefix() -> None:
    inner = SpyEmbeddingModel()

    await _embedder(inner).embed_documents(["Раздел «Показания».", "Раздел «Способ применения»."])

    assert inner.sent == ["Раздел «Показания».", "Раздел «Способ применения»."]


async def test_result_is_keyed_by_caller_text() -> None:
    result = await _embedder(SpyEmbeddingModel()).embed_query("Дозировка")

    assert result.inputs == ["Дозировка"]
    assert result["Дозировка"] == result.embeddings[0]


def test_build_embedder_requires_node_urls() -> None:
    with pytest.raises(AiNotConfiguredError, match="COMPASS_EMBEDDINGS_URLS"):
        _build(_settings())


async def test_built_embedder_sends_model_card_prefix_to_server() -> None:
    async with fake_node(_llama) as node:
        await _build(_settings(node.url)).embed_query("Дозировка")

    [request] = node.requests
    assert request.path == "/v1/embeddings"
    assert request.json()["model"] == "giga-embeddings-3b-0826"
    assert request.json()["input"] == [
        "Instruct: Given a query, retrieve relevant passages\nQuery: Дозировка"
    ]


async def test_built_embedder_does_not_retry_server_error() -> None:
    # Повторы — дело очереди; клиент отдаёт ошибку сразу.
    async with fake_node(_server_error) as node:
        with pytest.raises(AiServiceError) as caught:
            await _build(_settings(node.url)).embed_documents(["фрагмент"])

    assert caught.value.status == 500
    assert caught.value.__cause__ is None

    assert len(node.requests) == 1


async def test_built_embedder_gives_up_after_timeout_without_retry() -> None:
    async def hang(request: Request) -> Reply:
        await asyncio.sleep(1)
        return _llama(request)

    async with fake_node(hang) as node:
        embedder = _build(_settings(node.url, embeddings_timeout_seconds=0.2))
        with pytest.raises(AiNoAnswerError) as caught:
            await embedder.embed_query("Дозировка")

    assert len(node.requests) == 1
    assert caught.value.__cause__ is None


async def test_dropped_connection_raises_no_answer_without_next_node() -> None:
    async with fake_node(lambda request: None) as dropping, fake_node(_llama) as spare:
        embedder = _build(_settings(dropping.url, spare.url))
        with pytest.raises(AiNoAnswerError):
            await embedder.embed_documents(["фрагмент"])

    assert spare.requests == []


async def test_built_embedder_does_not_send_ambient_openai_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_ambient_openai_env(monkeypatch)

    async with fake_node(_llama) as node:
        await _build(_settings(node.url)).embed_query("Дозировка")

    assert_no_ambient_openai_values(node.requests[0])


async def test_answer_comes_from_next_node_when_first_is_down() -> None:
    recorder = MemoryRecorder()

    async with fake_node(_llama) as live:
        result = await _build(_settings(dead_node_url(), live.url), recorder).embed_query(
            "Дозировка"
        )

    assert list(result.embeddings[0]) == [0.6, 0.8]
    [run] = recorder.runs
    assert (run.role, run.task, run.status, run.node) == (
        "embeddings",
        "knowledge.search",
        "ok",
        live.url,
    )
    assert run.model == "giga-embeddings-3b-0826"
    assert run.params == {"input_type": "query", "inputs": 1}
    assert run.input_tokens == 7
    assert "Дозировка" not in repr(run)


async def test_server_error_is_not_sent_to_next_node() -> None:
    recorder = MemoryRecorder()

    async with fake_node(_server_error) as broken, fake_node(_llama) as spare:
        with pytest.raises(AiServiceError):
            await _build(_settings(broken.url, spare.url), recorder).embed_documents(["фрагмент"])

    assert spare.requests == []
    assert (recorder.runs[0].status, recorder.runs[0].error) == ("error", "AiServiceError 500")


async def test_all_nodes_down_raises_nodes_unavailable_and_is_recorded() -> None:
    recorder = MemoryRecorder()
    embedder = _build(_settings(dead_node_url(), dead_node_url()), recorder)

    with pytest.raises(NodesUnavailableError):
        await embedder.embed_documents(["фрагмент"])

    [run] = recorder.runs
    assert (run.status, run.error, run.node) == ("error", "NodesUnavailableError", None)


async def test_node_switch_is_logged_without_request_content() -> None:
    dead = dead_node_url()

    async with fake_node(_llama) as live:
        embedder = _build(_settings(dead, live.url))
        with structlog.testing.capture_logs() as logs:
            await embedder.embed_query("Дозировка")

    assert logs == [
        {
            "event": "ai.node_unavailable",
            "log_level": "warning",
            "role": "embeddings",
            "node": dead,
            "error": "ModelAPIError",
        }
    ]
