"""Варианты подбора v3 (спека #92, раздел 5): имя, запросы поиска и выдача.

Вариант — набор фич: переписывание вопроса (`rw`), шкалы в индексе (`scales`), порог балла
ранжировщика (`min`), правила для ловушек (`premise`), проверка утверждений (`verify`),
рассуждения (`think`). База — боевая конфигурация после #70: вопрос как есть, без шкал,
bge-reranker-v2-m3 на первых 20, без порога.

Выдача повторяет боевой `service.answer`: поиск по каждому запросу, слияние `fuse_queries`,
ранжировщик первых `RERANK_DEPTH` по запросу ранжировщика, порог. Вариант без шкал ищет
по индексу без фрагментов приложения Г: движок для него собирается на этих фрагментах.
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Protocol, cast
from uuid import UUID

from clin_compass.modules.knowledge.answering import Thinking
from clin_compass.modules.knowledge.rewriting import RerankQuery, Rewrite, distinct_rewrites
from clin_compass.modules.knowledge.search import CANDIDATES, RERANK_DEPTH, fuse_queries

from retrieval import Config, Engine

RERANKER = "bge-v2-m3"
SCALE_SECTION = "/doc_g/"


@dataclass(frozen=True)
class Variant:
    """Набор фич варианта; по умолчанию — боевая конфигурация до #92."""

    rewrite: Rewrite = "off"
    scales: bool = False
    min_score: float | None = None
    premise: bool = False
    verify: bool = False
    thinking: Thinking = "off"
    # Спека #105: ранжирование по исходному вопросу, вопросы к фрагментам, аспекты, лимит
    # утверждений (None — боевой).
    rerank_query: RerankQuery = "rewrite"
    d2q: bool = False
    aspects: bool = False
    claims: int | None = None

    @property
    def retrieval_name(self) -> str:
        """Имя только поисковых фич; без них — `base`."""
        parts = []
        if self.d2q:
            parts.append("d2q")
        if self.rewrite != "off":
            parts.append(f"rw={self.rewrite}")
        if self.rerank_query != "rewrite":
            parts.append(f"rq={self.rerank_query}")
        if self.scales:
            parts.append("scales")
        if self.min_score is not None:
            parts.append(f"min={self.min_score}")
        return "+".join(parts) or "base"

    @property
    def name(self) -> str:
        """Короткое имя: фичи, отличные от базы; база — `base`."""
        parts = [] if self.retrieval_name == "base" else [self.retrieval_name]
        if self.premise:
            parts.append("premise")
        if self.verify:
            parts.append("verify")
        if self.thinking != "off":
            parts.append(f"think={self.thinking}")
        if self.aspects:
            parts.append("aspects")
        if self.claims is not None:
            parts.append(f"claims={self.claims}")
        return "+".join(parts) or "base"

    @classmethod
    def from_name(cls, name: str) -> "Variant":
        """Вариант по имени `name`."""
        variant = cls()
        if name == "base":
            return variant
        for part in name.split("+"):
            factor, _, value = part.partition("=")
            if factor == "rw":
                variant = replace(variant, rewrite=cast(Rewrite, value))
            elif factor == "scales":
                variant = replace(variant, scales=True)
            elif factor == "min":
                variant = replace(variant, min_score=float(value))
            elif factor == "premise":
                variant = replace(variant, premise=True)
            elif factor == "verify":
                variant = replace(variant, verify=True)
            elif factor == "think":
                variant = replace(variant, thinking=cast(Thinking, value))
            elif factor == "rq":
                variant = replace(variant, rerank_query=cast(RerankQuery, value))
            elif factor == "d2q":
                variant = replace(variant, d2q=True)
            elif factor == "aspects":
                variant = replace(variant, aspects=True)
            elif factor == "claims":
                variant = replace(variant, claims=int(value))
            else:
                raise ValueError(f"неизвестная фича в имени варианта: {part}")
        return variant


def plan(
    variant: Variant, question: str, patient: str | None, rewrites: Sequence[str]
) -> tuple[list[str], str]:
    """Запросы поиска и запрос ранжировщика — как `KnowledgeAi.queries` в бою."""
    if variant.rewrite == "patient" and patient:
        joined = f"{question}\n{patient}"
        return [joined], joined
    if variant.rewrite in ("one", "multi"):
        rewrites = distinct_rewrites(question, rewrites)
        if rewrites:
            rerank = question if variant.rerank_query == "question" else rewrites[0]
            return [question, *rewrites], rerank
    return [question], question


class QuestionSearch(Protocol):
    """Поиск по вопросам к фрагментам: до 40 ключей фрагментов на запрос."""

    def vector(self, query: str) -> list[str]:
        """По лучшему из вопросов фрагмента по косинусной близости."""
        ...

    def full_text(self, query: str) -> list[str]:
        """По лучшему из вопросов фрагмента в полнотекстовом поиске."""
        ...


def _ranked(engine: Engine, query: str, questions: QuestionSearch | None) -> list[str]:
    """Выдача одного запроса.

    Без вопросов к фрагментам — гибридный поиск из двух списков, как в бою. С ними — четыре
    списка, слитые `fuse_queries` по спеке #105, 5.1. Боевого поиска по четырём спискам нет.
    """
    if questions is None:
        return engine.rank(Config(), query)
    lists = [
        engine.sources.vector(Config(), query)[:CANDIDATES],
        engine.sources.full_text(Config(), query)[:CANDIDATES],
        questions.vector(query)[:CANDIDATES],
        questions.full_text(query)[:CANDIDATES],
    ]
    return [str(k) for k in fuse_queries(cast(list[list[UUID]], lists))]


def rank(
    engine: Engine,
    queries: Sequence[str],
    rerank_query: str,
    *,
    min_score: float | None,
    questions: QuestionSearch | None = None,
) -> tuple[list[str], dict[str, float]]:
    """Ключи выдачи по порядку и баллы ранжировщика первых `RERANK_DEPTH`.

    С вопросами к фрагментам (`questions`) каждый запрос даёт четыре списка, они сливаются
    `fuse_queries`. С порогом — только фрагменты с баллом не ниже него.
    """
    lists = [_ranked(engine, query, questions) for query in queries]
    fused = (
        [str(k) for k in fuse_queries(cast(list[list[UUID]], lists))]
        if len(lists) > 1
        else lists[0]
    )
    head, tail = fused[:RERANK_DEPTH], fused[RERANK_DEPTH:]
    scores = engine.sources.rerank(RERANKER, rerank_query, head)
    position = {key: i for i, key in enumerate(head)}
    ordered = sorted(head, key=lambda k: (-scores[k], position[k]))
    if min_score is not None:
        return [k for k in ordered if scores[k] >= min_score], scores
    return ordered + tail, scores
