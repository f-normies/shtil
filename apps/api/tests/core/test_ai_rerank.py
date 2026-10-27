"""Клиент ранжировщика: баллы документов через services/rerank, узлы по порядку (спека #70, 7)."""

from typing import Any

import pytest

from clin_compass.core.ai.nodes import (
    AiNoAnswerError,
    AiNotConfiguredError,
    AiOutputError,
    AiServiceError,
    NodesUnavailableError,
)
from clin_compass.core.ai.rerank import MODEL_ALIAS, build_reranker
from clin_compass.core.config import Settings
from tests.core.fake_nodes import MemoryRecorder, Reply, Request, dead_node_url, fake_node

QUERY = "Можно ли Авиандр пациентке 34 лет при беременности?"
DOCUMENTS = ["Противопоказания: беременность.", "Принимать по 20 мг в сутки."]


def _rerank(request: Request) -> Reply:
    results = [{"index": 1, "relevance_score": -3.5}, {"index": 0, "relevance_score": 7.25}]
    return 200, {"results": results}


def _settings(*urls: str, **overrides: Any) -> Settings:
    # Значения передаём явно: Makefile экспортирует .env, где может стоять настоящий адрес.
    return Settings(
        database_url="postgresql+asyncpg://unused/none",
        environment="test",
        rerank_urls=urls,
        **overrides,
    )


async def test_scores_follow_document_order_and_run_is_recorded_without_text() -> None:
    recorder = MemoryRecorder()

    async with fake_node(_rerank) as node:
        reranker = build_reranker(_settings(node.url), recorder)
        result = await reranker.rerank(QUERY, DOCUMENTS, task="knowledge.search")

    assert result == [7.25, -3.5]
    [request] = node.requests
    assert request.path == "/v1/rerank"
    assert request.json() == {"query": QUERY, "documents": DOCUMENTS, "top_n": 2}
    [run] = recorder.runs
    assert (run.role, run.task, run.status, run.node, run.model) == (
        "rerank",
        "knowledge.search",
        "ok",
        node.url,
        MODEL_ALIAS,
    )
    assert run.params == {"documents": 2}
    assert "Авиандр" not in repr(run)


async def test_empty_documents_need_no_node() -> None:
    recorder = MemoryRecorder()
    reranker = build_reranker(_settings(dead_node_url()), recorder)

    result = await reranker.rerank(QUERY, [], task="knowledge.search")

    assert result == []
    assert recorder.runs == []


async def test_dead_node_is_skipped() -> None:
    async with fake_node(_rerank) as node:
        reranker = build_reranker(_settings(dead_node_url(), node.url), MemoryRecorder())
        result = await reranker.rerank(QUERY, DOCUMENTS, task="knowledge.search")

    assert result == [7.25, -3.5]


async def test_all_nodes_dead_raise_unavailable() -> None:
    reranker = build_reranker(_settings(dead_node_url()), MemoryRecorder())

    with pytest.raises(NodesUnavailableError):
        await reranker.rerank(QUERY, DOCUMENTS, task="knowledge.search")


@pytest.mark.parametrize(
    ("reply", "error"),
    [
        ((500, {"error": "boom"}), AiServiceError),
        ((200, {"results": [{"index": 0, "relevance_score": 1.0}]}), AiOutputError),
        ((200, {"unexpected": True}), AiOutputError),
        # Повтор номера: документ 0 получил бы балл дважды.
        (
            (
                200,
                {
                    "results": [
                        {"index": 0, "relevance_score": 1.0},
                        {"index": 1, "relevance_score": 2.0},
                        {"index": 0, "relevance_score": 9.0},
                    ]
                },
            ),
            AiOutputError,
        ),
        # NaN ломает сортировку кандидатов.
        (
            (
                200,
                {
                    "results": [
                        {"index": 0, "relevance_score": float("nan")},
                        {"index": 1, "relevance_score": 1.0},
                    ]
                },
            ),
            AiOutputError,
        ),
    ],
)
async def test_bad_replies_raise_without_switching_node(
    reply: Reply, error: type[Exception]
) -> None:
    async with fake_node(lambda request: reply) as broken, fake_node(_rerank) as spare:
        reranker = build_reranker(_settings(broken.url, spare.url), MemoryRecorder())
        with pytest.raises(error) as caught:
            await reranker.rerank(QUERY, DOCUMENTS, task="knowledge.search")

    assert spare.requests == []
    assert "Авиандр" not in str(caught.value)


async def test_slow_node_raises_no_answer() -> None:
    import asyncio

    async def slow(request: Request) -> Reply:
        await asyncio.sleep(1)
        return _rerank(request)

    async with fake_node(slow) as node:
        reranker = build_reranker(_settings(node.url, rerank_timeout_seconds=0.1), MemoryRecorder())
        with pytest.raises(AiNoAnswerError):
            await reranker.rerank(QUERY, DOCUMENTS, task="knowledge.search")


def test_reranker_requires_node_urls() -> None:
    with pytest.raises(AiNotConfiguredError):
        build_reranker(_settings(), MemoryRecorder())
