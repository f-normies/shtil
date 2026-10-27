"""Клиент ранжировщика: баллы фрагментов для вопроса через `services/rerank` (спека #70, 7).

Запрос — `POST <узел>/v1/rerank` llama-server: `{"query", "documents", "top_n"}`, ответ —
`results` с индексом документа и баллом. Баллы bge-reranker — сырые логиты: важен порядок,
шкала не важна. Вопрос врача может содержать данные пациента: в ai_runs попадают только
хеш входа и число документов.
"""

import json
import math
from collections.abc import Sequence

import httpx2
from pydantic import BaseModel, ValidationError

from clin_compass.core.ai.nodes import (
    AiNoAnswerError,
    AiOutputError,
    AiServiceError,
    NodesUnavailableError,
    is_unavailable,
    node_urls,
    report_unavailable,
)
from clin_compass.core.ai.runs import RunRecorder, hash_input, track_run
from clin_compass.core.config import Settings

# Имя модели в llama-server: флаг -a в services/rerank/serve.sh (MODEL_ALIAS в versions.env).
MODEL_ALIAS = "bge-reranker-v2-m3"


class _Result(BaseModel):
    index: int
    relevance_score: float


class _Reply(BaseModel):
    results: list[_Result]


class Reranker:
    """Баллы документов на узлах ранжировщика по порядку, с записью каждого вызова в ai_runs."""

    def __init__(
        self, urls: Sequence[str], recorder: RunRecorder, *, timeout: httpx2.Timeout
    ) -> None:
        self._urls = tuple(urls)
        self._recorder = recorder
        self._timeout = timeout

    async def rerank(self, query: str, documents: Sequence[str], *, task: str) -> list[float]:
        """Балл каждого документа для запроса, в порядке `documents`.

        Вызов пишется в ai_runs; его id поиску не нужен. Без документов узел не вызывается
        и ai_runs не пишется.

        Raises:
            NodesUnavailableError: Ни один узел ранжировщика не принял запрос.
            AiServiceError: Узел ответил ошибкой.
            AiNoAnswerError: Узел принял запрос, но не ответил: таймаут ответа или обрыв.
            AiOutputError: Ответ не по схеме или баллов не по одному на документ.
        """
        if not documents:
            return []
        body = {"query": query, "documents": list(documents), "top_n": len(documents)}
        async with (
            track_run(
                self._recorder,
                role="rerank",
                task=task,
                model=MODEL_ALIAS,
                input_hash=hash_input(json.dumps(body, ensure_ascii=False).encode()),
                params={"documents": len(documents)},
            ) as run,
            httpx2.AsyncClient(timeout=self._timeout) as client,
        ):
            for url in self._urls:
                try:
                    response = await client.post(f"{url}/v1/rerank", json=body)
                    response.raise_for_status()
                except httpx2.HTTPStatusError as exc:
                    if is_unavailable(exc):
                        report_unavailable("rerank", url, exc)
                        continue
                    # from None: в цепочке был бы ответ узла с телом.
                    raise AiServiceError("rerank", exc.response.status_code) from None
                except httpx2.TransportError as exc:
                    if is_unavailable(exc):
                        report_unavailable("rerank", url, exc)
                        continue
                    raise AiNoAnswerError("rerank") from None
                scores = _scores(response, len(documents))
                run.node = url
                break
            else:
                raise NodesUnavailableError("rerank")
        return scores


def _scores(response: httpx2.Response, count: int) -> list[float]:
    try:
        reply = _Reply.model_validate(response.json())
    except (json.JSONDecodeError, UnicodeDecodeError, ValidationError):
        raise AiOutputError("rerank") from None
    by_index = {item.index: item.relevance_score for item in reply.results}
    # Ровно один конечный балл на документ: повтор номера или NaN сломали бы порядок.
    if (
        len(reply.results) != count
        or sorted(by_index) != list(range(count))
        or not all(math.isfinite(score) for score in by_index.values())
    ):
        raise AiOutputError("rerank")
    return [by_index[i] for i in range(count)]


def build_reranker(settings: Settings, recorder: RunRecorder) -> Reranker:
    """Собирает клиент ранжировщика из узлов `COMPASS_RERANK_URLS`.

    Raises:
        AiNotConfiguredError: Адреса узлов ранжировщика не заданы.
    """
    timeout = httpx2.Timeout(
        settings.rerank_timeout_seconds, connect=settings.ai_connect_timeout_seconds
    )
    return Reranker(node_urls(settings, "rerank"), recorder, timeout=timeout)


__all__ = ["MODEL_ALIAS", "Reranker", "build_reranker"]
