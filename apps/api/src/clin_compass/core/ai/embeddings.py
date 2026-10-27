"""Клиент эмбеддингов: Giga-Embeddings в llama-server по OpenAI-совместимому `/v1/embeddings`.

Giga-Embeddings кодирует запрос и документ по-разному. К запросу добавляется
`Instruct: {инструкция}\\nQuery: `, документ идёт без префикса. Префикс ставит только этот модуль
(спека #16, раздел 4): вызывающий код пользуется `embed_query()` и `embed_documents()`.

Цепочка (спека #18, раздел 5): запись в ai_runs → префикс → перебор узлов по порядку.
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace

import httpx2
from pydantic_ai import Embedder
from pydantic_ai.embeddings import (
    EmbeddingModel,
    EmbeddingResult,
    EmbeddingSettings,
    WrapperEmbeddingModel,
)
from pydantic_ai.embeddings.openai import OpenAIEmbeddingModel
from pydantic_ai.embeddings.result import EmbedInputType
from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError
from pydantic_ai.providers.openai import OpenAIProvider

from clin_compass.core.ai.nodes import (
    AiNoAnswerError,
    AiServiceError,
    NodesUnavailableError,
    is_unavailable,
    node_urls,
    openai_client,
    report_unavailable,
)
from clin_compass.core.ai.runs import RunRecorder, hash_input, track_run
from clin_compass.core.config import Settings

# Имя модели в llama-server: флаг -a в services/embeddings/serve.sh (MODEL_ALIAS в versions.env).
MODEL_ALIAS = "giga-embeddings-3b-0826"
# Инструкция для поиска из config_sentence_transformers.json модели (prompts.query).
RETRIEVAL_INSTRUCTION = "Given a query, retrieve relevant passages"


@dataclass(init=False)
class InstructEmbeddingModel(WrapperEmbeddingModel):
    """Ставит перед запросами префикс инструкции, документы передаёт без изменений."""

    instruction: str

    def __init__(
        self, wrapped: EmbeddingModel, *, instruction: str = RETRIEVAL_INSTRUCTION
    ) -> None:
        super().__init__(wrapped)
        self.instruction = instruction

    async def embed(
        self,
        inputs: str | Sequence[str],
        *,
        input_type: EmbedInputType,
        settings: EmbeddingSettings | None = None,
    ) -> EmbeddingResult:
        texts = [inputs] if isinstance(inputs, str) else list(inputs)
        if input_type != "query":
            return await self.wrapped.embed(texts, input_type=input_type, settings=settings)

        prefix = f"Instruct: {self.instruction}\nQuery: "
        result = await self.wrapped.embed(
            [prefix + text for text in texts], input_type=input_type, settings=settings
        )
        # В результате — тексты вызывающего кода: по ним работает поиск result[текст].
        return replace(result, inputs=texts)


class FallbackEmbeddingModel(EmbeddingModel):
    """Перебирает узлы по порядку, пока один не примет запрос (`is_unavailable`).

    Узел, который ответил, кладёт в `provider_details["node"]` результата. Ошибку HTTP узла
    отдаёт как `AiServiceError`, таймаут ответа и обрыв соединения — как `AiNoAnswerError`.
    """

    def __init__(self, nodes: Sequence[tuple[str, EmbeddingModel]]) -> None:
        super().__init__()
        self._nodes = list(nodes)

    @property
    def model_name(self) -> str:
        return self._nodes[0][1].model_name

    @property
    def system(self) -> str:
        return self._nodes[0][1].system

    async def embed(
        self,
        inputs: str | Sequence[str],
        *,
        input_type: EmbedInputType,
        settings: EmbeddingSettings | None = None,
    ) -> EmbeddingResult:
        for url, model in self._nodes:
            try:
                result = await model.embed(inputs, input_type=input_type, settings=settings)
            except ModelAPIError as exc:
                if is_unavailable(exc):
                    report_unavailable("embeddings", url, exc)
                    continue
                if isinstance(exc, ModelHTTPError):
                    # Тело ответа в ошибку не берём: в нём узел может повторить вход.
                    raise AiServiceError("embeddings", exc.status_code) from None
                # Таймаут ответа или обрыв: запрос мог дойти до модели.
                raise AiNoAnswerError("embeddings") from None
            details = {**(result.provider_details or {}), "node": url}
            return replace(result, provider_details=details)
        raise NodesUnavailableError("embeddings")


@dataclass(init=False)
class RecordedEmbeddingModel(WrapperEmbeddingModel):
    """Пишет каждый вызов в ai_runs: тип входа, число текстов, токены, хеш текстов."""

    recorder: RunRecorder
    task: str

    def __init__(self, wrapped: EmbeddingModel, *, recorder: RunRecorder, task: str) -> None:
        super().__init__(wrapped)
        self.recorder = recorder
        self.task = task

    async def embed(
        self,
        inputs: str | Sequence[str],
        *,
        input_type: EmbedInputType,
        settings: EmbeddingSettings | None = None,
    ) -> EmbeddingResult:
        texts = [inputs] if isinstance(inputs, str) else list(inputs)
        async with track_run(
            self.recorder,
            role="embeddings",
            task=self.task,
            model=MODEL_ALIAS,
            input_hash=hash_input("\0".join(texts)),
            params={"input_type": input_type, "inputs": len(texts)},
        ) as run:
            result = await self.wrapped.embed(texts, input_type=input_type, settings=settings)
            run.node = (result.provider_details or {}).get("node")
            run.input_tokens = result.usage.input_tokens
        return result


def build_embedder(settings: Settings, recorder: RunRecorder, *, task: str) -> Embedder:
    """Собирает клиент эмбеддингов для поиска по базе знаний.

    Args:
        settings: Настройки приложения; нужны `embeddings_urls` и таймауты.
        recorder: Куда писать вызовы; в приложении — `DbRunRecorder`.
        task: Имя задачи для ai_runs, например `knowledge.search` или `knowledge.index`.

    Ошибку сервера (`AiServiceError`), таймаут ответа и обрыв соединения (`AiNoAnswerError`)
    клиент отдаёт сразу, без повторов: повторяет очередь.

    Returns:
        Embedder: `embed_query()` добавляет префикс инструкции, `embed_documents()` — нет.
        `max_input_tokens()` и `count_tokens()` не настоящие: лимит и токенизатор llama-server
        клиент не знает. Длину фрагментов ограничивает нарезка (#27).

    Raises:
        AiNotConfiguredError: Адреса узлов эмбеддингов не заданы.
    """
    timeout = httpx2.Timeout(
        settings.embeddings_timeout_seconds, connect=settings.ai_connect_timeout_seconds
    )
    nodes: list[tuple[str, EmbeddingModel]] = []
    for url in node_urls(settings, "embeddings"):
        provider = OpenAIProvider(openai_client=openai_client(url, timeout))
        nodes.append((url, OpenAIEmbeddingModel(MODEL_ALIAS, provider=provider)))
    model = InstructEmbeddingModel(FallbackEmbeddingModel(nodes))
    return Embedder(RecordedEmbeddingModel(model, recorder=recorder, task=task))


__all__ = [
    "MODEL_ALIAS",
    "RETRIEVAL_INSTRUCTION",
    "FallbackEmbeddingModel",
    "InstructEmbeddingModel",
    "RecordedEmbeddingModel",
    "build_embedder",
]
