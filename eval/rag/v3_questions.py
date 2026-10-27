"""Вопросы к фрагментам в подборе (спека #105, 5.1): кэш генерации и поиск по ним.

Вопросы пишет боевой `Questioner` и кладёт в кэш по тексту фрагмента и версии промпта.
Векторный поиск: фрагмент ранжируется по лучшему из своих вопросов. Полнотекстовый: тот же
`ts_rank_cd`, что в бою, по лучшему из вопросов фрагмента, при равенстве — по id фрагмента.
Векторы вопросов — как у документов, без инструкции запроса.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import asyncpg
import numpy as np
from clin_compass.modules.knowledge.search import CANDIDATES

from cache import CACHE, JsonStore, VectorStore, text_key
from retrieval import Doc, cosine_ranking

QUESTIONS_CACHE = CACHE / "fragment_questions.json"

_FULL_TEXT = """
SELECT key FROM fq_questions
WHERE tsv @@ replace(plainto_tsquery('russian', $1)::text, ' & ', ' | ')::tsquery
GROUP BY key, fragment_id
ORDER BY max(ts_rank_cd(tsv, replace(plainto_tsquery('russian', $1)::text, ' & ', ' | ')::tsquery))
  DESC, fragment_id
LIMIT 40
"""


def questions_key(version: str, doc: Doc) -> str:
    """Ключ кэша вопросов: версия промпта, название, путь заголовков и текст фрагмента."""
    return text_key("fq", f"{version}\n{doc.title}\n{doc.heading_path}\n{doc.text}")


def cached_questions(
    store: JsonStore, version: str, docs: Mapping[str, Doc]
) -> dict[str, list[str]]:
    """Вопросы фрагментов, которые уже есть в кэше, по ключу фрагмента."""
    found = {}
    for key, doc in docs.items():
        cache_key = questions_key(version, doc)
        if not store.missing([cache_key]):
            found[key] = list(store.get(cache_key))
    return found


@dataclass(frozen=True)
class QuestionIndex:
    """Поиск по вопросам к фрагментам: векторы из кэша и готовые выдачи полнотекстового поиска."""

    matrix: Mapping[str, np.ndarray]
    vectors: VectorStore
    text_rankings: Mapping[str, list[str]]

    @classmethod
    def build(
        cls,
        by_key: Mapping[str, Sequence[str]],
        vectors: VectorStore,
        text_rankings: Mapping[str, list[str]],
    ) -> "QuestionIndex":
        matrix = {
            key: np.stack([vectors.get(text_key("doc", q)) for q in questions])
            for key, questions in by_key.items()
            if questions
        }
        return cls(matrix=matrix, vectors=vectors, text_rankings=text_rankings)

    def vector(self, query: str) -> list[str]:
        """До 40 фрагментов по лучшему из их вопросов."""
        return cosine_ranking(
            self.vectors.get(text_key("query", query)), self.matrix, limit=CANDIDATES
        )

    def full_text(self, query: str) -> list[str]:
        """До 40 фрагментов полнотекстового поиска по их вопросам."""
        return self.text_rankings[query]


async def question_text_rankings(
    database_url: str,
    docs: Mapping[str, Doc],
    by_key: Mapping[str, Sequence[str]],
    queries: Sequence[str],
) -> dict[str, list[str]]:
    """Выдача полнотекстового поиска по вопросам к фрагментам для каждого запроса."""
    conn = await asyncpg.connect(database_url.replace("+asyncpg", ""))
    try:
        await conn.execute(
            "CREATE TEMP TABLE fq_questions (key text, fragment_id uuid, tsv tsvector)"
        )
        rows = [
            (key, docs[key].fragment_id, question)
            for key, questions in by_key.items()
            if key in docs
            for question in questions
        ]
        await conn.executemany(
            "INSERT INTO fq_questions VALUES ($1, $2::uuid, to_tsvector('russian', $3))", rows
        )
        result = {}
        for query in dict.fromkeys(queries):
            found = await conn.fetch(_FULL_TEXT, query)
            result[query] = [row["key"] for row in found]
        return result
    finally:
        await conn.close()
