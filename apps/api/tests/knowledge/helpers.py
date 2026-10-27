"""Общие помощники тестов модуля knowledge: документы в базе и детерминированные эмбеддинги.

Модели и сеть не вызываются. Эмбеддинг — мешок слов: каждое слово текста даёт единицу
в своём измерении вектора 2048. Тексты с общими словами близки, без общих слов — далеки.
"""

import hashlib
import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic_ai import Embedder
from pydantic_ai.embeddings import EmbeddingModel, EmbeddingResult, EmbeddingSettings
from pydantic_ai.embeddings.result import EmbedInputType
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import RequestUsage
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession

from clin_compass.core.ai.llm import MODEL_ALIAS
from clin_compass.modules.knowledge.models import Document, Fragment

DIMENSIONS = 2048
_WORD = re.compile(r"\w+")


def words_vector(text: str) -> list[float]:
    """Вектор текста: по измерению на слово; нулевое измерение — фон, вектор не бывает нулевым."""
    vector = [0.0] * DIMENSIONS
    vector[0] = 0.01
    for word in _WORD.findall(text.lower()):
        digest = hashlib.sha256(word.encode()).digest()
        vector[1 + int.from_bytes(digest[:4]) % (DIMENSIONS - 1)] += 1.0
    return vector


class WordsEmbeddingModel(EmbeddingModel):
    """Эмбеддинги «мешок слов». Запоминает, что и с каким типом входа ушло в модель."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[EmbedInputType, list[str]]] = []

    @property
    def model_name(self) -> str:
        return "words"

    @property
    def system(self) -> str:
        return "test"

    async def embed(
        self,
        inputs: str | Sequence[str],
        *,
        input_type: EmbedInputType,
        settings: EmbeddingSettings | None = None,
    ) -> EmbeddingResult:
        texts = [inputs] if isinstance(inputs, str) else list(inputs)
        self.calls.append((input_type, texts))
        return EmbeddingResult(
            [words_vector(text) for text in texts],
            inputs=texts,
            input_type=input_type,
            model_name=self.model_name,
            provider_name=self.system,
            usage=RequestUsage(input_tokens=len(texts)),
        )


def words_embedder() -> tuple[Embedder, WordsEmbeddingModel]:
    """Embedder на модели «мешок слов» и сама модель для проверок."""
    model = WordsEmbeddingModel()
    return Embedder(model), model


def probe_model(answer: dict[str, Any], on_call: Callable[[], None]) -> FunctionModel:
    """LLM, которая перед ответом вызывает `on_call`: так тест видит состояние во время вызова."""

    async def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        on_call()
        return ModelResponse(parts=[TextPart(json.dumps(answer, ensure_ascii=False))])

    return FunctionModel(respond, model_name=MODEL_ALIAS)


@dataclass(frozen=True)
class Piece:
    """Фрагмент для записи в базу напрямую, без индексации."""

    text: str
    section: str = "doc_3"
    section_title: str = "Лечение"
    heading_path: str = "Лечение"
    kind: str = "text"
    uur: str | None = None
    udd: int | None = None
    embedding: list[float] | None = None


async def add_document(
    session: AsyncSession,
    source_id: str,
    pieces: Sequence[Piece] = (),
    *,
    status: str = "active",
    title: str = "Тестовые рекомендации",
    version: str = "1",
    kind: str = "clinical_guideline",
    content_hash: str = "0" * 64,
) -> tuple[Document, list[Fragment]]:
    """Документ с фрагментами; ключи — `{source_id}/{раздел}/{номер в разделе}`."""
    document = Document(
        source_id=source_id,
        version=version,
        title=title,
        url=f"https://example.org/{source_id}",
        kind=kind,
        content_hash=content_hash,
        status=status,
        superseded_at=func.now() if status == "superseded" else None,
    )
    session.add(document)
    await session.flush()
    fragments = []
    numbers: dict[str, int] = {}
    for position, piece in enumerate(pieces, start=1):
        numbers[piece.section] = numbers.get(piece.section, 0) + 1
        fragment = Fragment(
            document_id=document.id,
            position=position,
            key=f"{source_id}/{piece.section}/{numbers[piece.section]}",
            section=piece.section,
            section_title=piece.section_title,
            heading_path=piece.heading_path,
            kind=piece.kind,
            text=piece.text,
            uur=piece.uur,
            udd=piece.udd,
            embedding=piece.embedding or words_vector(f"{piece.heading_path}\n{piece.text}"),
        )
        session.add(fragment)
        fragments.append(fragment)
    await session.flush()
    return document, fragments
