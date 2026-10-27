"""Выдача поиска по конфигурации подбора (спека #70, 5.3).

Конфигурация — набор факторов: модель эмбеддингов, раскрытие сокращений, выбор документа,
части фрагментов, контекст, ранжировщик. Векторный и полнотекстовый поиски дают по 40 лучших,
слияние — боевая функция `fuse`. Источник векторов и оценок (`Sources`) подставляется:
в `tune.py` он читает кэш и базу, в тестах — заданные ответы.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Literal, Protocol, cast
from uuid import UUID

import numpy as np
from clin_compass.modules.knowledge.search import CANDIDATES, RRF_K, Fused, fuse

from variants import Abbreviations, chosen_sources

Routing = Literal["off", "bonus", "filter"]
BASE_EMBEDDINGS = "giga-3b"


@dataclass(frozen=True)
class Config:
    """Конфигурация поиска. По умолчанию — как в боевом коде #27."""

    embeddings: str = BASE_EMBEDDINGS
    abbreviations: bool = False
    routing: Routing = "off"
    parts: bool = False
    context: bool = False
    reranker: str | None = None
    rerank_depth: int = 40

    @property
    def name(self) -> str:
        """Короткое имя: только факторы, отличные от базы; база — `base`."""
        changed = []
        if self.embeddings != BASE_EMBEDDINGS:
            changed.append(f"emb={self.embeddings}")
        if self.abbreviations:
            changed.append("abbr")
        if self.routing != "off":
            changed.append(f"route={self.routing}")
        if self.parts:
            changed.append("parts")
        if self.context:
            changed.append("ctx")
        if self.reranker is not None:
            changed.append(f"rerank={self.reranker}@{self.rerank_depth}")
        return "+".join(changed) or "base"

    @classmethod
    def from_name(cls, name: str) -> "Config":
        """Конфигурация по имени `name`."""
        config = cls()
        if name == "base":
            return config
        for part in name.split("+"):
            factor, _, value = part.partition("=")
            if factor == "emb":
                config = replace(config, embeddings=value)
            elif factor == "abbr":
                config = replace(config, abbreviations=True)
            elif factor == "route":
                config = replace(config, routing=cast(Routing, value))
            elif factor == "parts":
                config = replace(config, parts=True)
            elif factor == "ctx":
                config = replace(config, context=True)
            elif factor == "rerank":
                model, _, depth = value.partition("@")
                config = replace(config, reranker=model, rerank_depth=int(depth))
            else:
                raise ValueError(f"неизвестный фактор в имени конфигурации: {part}")
        return config


@dataclass(frozen=True)
class Doc:
    """Фрагмент индекса, как его видит подбор."""

    key: str
    source_id: str
    title: str
    heading_path: str
    text: str
    # id фрагмента в базе: полнотекстовый поиск при равном балле упорядочивает по нему, как в бою.
    fragment_id: str = ""


class Sources(Protocol):
    """Откуда берутся выдачи поисков и оценки ранжировщика."""

    def vector(self, config: Config, query: str) -> list[str]:
        """До 40 ключей по косинусной близости к запросу."""
        ...

    def full_text(self, config: Config, query: str) -> list[str]:
        """До 40 ключей полнотекстового поиска, как в бою."""
        ...

    def rerank(self, model: str, query: str, keys: Sequence[str]) -> dict[str, float]:
        """Балл ранжировщика для каждого ключа."""
        ...


def cosine_ranking(query: np.ndarray, parts: Mapping[str, np.ndarray], *, limit: int) -> list[str]:
    """Ключи по лучшему косинусу среди частей фрагмента; при равенстве — по ключу."""
    q = query / np.linalg.norm(query)
    best = {}
    for key, matrix in parts.items():
        norms = np.linalg.norm(matrix, axis=1)
        best[key] = float(np.max(matrix @ q / norms))
    return sorted(best, key=lambda k: (-best[k], k))[:limit]


def _order(item: Fused) -> tuple[float, int, bool, str]:
    best = min(r for r in (item.vector_rank, item.text_rank) if r is not None)
    return -item.score, best, item.vector_rank is None, str(item.id)


@dataclass(frozen=True)
class Engine:
    """Собирает выдачу поиска по конфигурации."""

    docs: Mapping[str, Doc]
    sources: Sources
    abbreviations: Abbreviations
    terms: Mapping[str, Sequence[str]]

    def rank(self, config: Config, question: str) -> list[str]:
        """Ключи фрагментов по порядку выдачи."""
        vector_query = question
        text_query = question
        if config.abbreviations:
            vector_query = self.abbreviations.for_embedding(question)
            text_query = self.abbreviations.for_full_text(question)
        vector = self.sources.vector(config, vector_query)[:CANDIDATES]
        text = self.sources.full_text(config, text_query)[:CANDIDATES]
        fused = fuse(cast(Sequence[UUID], vector), cast(Sequence[UUID], text))
        fused = self._route(config, question, fused)
        keys = [str(item.id) for item in fused]
        if config.reranker is None:
            return keys
        head = keys[: config.rerank_depth]
        scores = self.sources.rerank(config.reranker, vector_query, head)
        position = {key: i for i, key in enumerate(head)}
        reranked = sorted(head, key=lambda k: (-scores[k], position[k]))
        return reranked + keys[config.rerank_depth :]

    def _route(self, config: Config, question: str, fused: list[Fused]) -> list[Fused]:
        if config.routing == "off":
            return fused
        chosen = chosen_sources(question, self.terms)
        if not chosen:
            return fused

        def picked(item: Fused) -> bool:
            return self.docs[str(item.id)].source_id in chosen

        if config.routing == "filter":
            return [item for item in fused if picked(item)]
        # Прибавка — балл первого места одного поиска.
        bonus = 1 / (RRF_K + 1)
        lifted = [
            replace(item, score=item.score + bonus) if picked(item) else item for item in fused
        ]
        return sorted(lifted, key=_order)
