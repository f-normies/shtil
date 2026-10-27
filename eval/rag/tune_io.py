"""Ввод-вывод подбора поиска (спека #70, 5.1).

- Клиенты llama-server: `/v1/embeddings` с префиксом запроса из карточки модели
  и `/v1/rerank`.
- Полнотекстовый поиск: тот же SQL, что в бою, по временной таблице с вариантами текста.
- `CacheSources` — выдачи для `retrieval.Engine` из кэша. В строгом режиме недостающий
  вектор или балл — ошибка. В режиме сбора недостающие пары ранжировщика копятся
  в `wanted`, и `tune.py rerank` досчитывает их.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import asyncpg
import httpx
import numpy as np
from clin_compass.core.db import make_engine, make_sessionmaker
from clin_compass.modules.knowledge import service

from cache import JsonStore, VectorStore, text_key
from retrieval import Config, Doc, cosine_ranking
from variants import document_text, split_parts

CANDIDATES = 40
PART_THRESHOLD = 1500
PART_LIMIT = 1200
# Инструкция Giga-Embeddings — та же, что у боевого клиента (`RETRIEVAL_INSTRUCTION`).
_GIGA = "Instruct: Given a query, retrieve relevant passages\nQuery: {text}"
# Формат Qwen3-Embedding из карточки модели: после «Query:» без пробела.
_QWEN = (
    "Instruct: Given a web search query, retrieve relevant passages that answer the query"
    "\nQuery:{text}"
)


@dataclass(frozen=True)
class EmbeddingModel:
    """Модель эмбеддингов: имя в API и шаблон запроса; None — запрос без префикса."""

    name: str
    query_template: str | None


EMBEDDING_MODELS = {
    "giga-3b": EmbeddingModel("giga-embeddings-3b-0826", _GIGA),
    "giga-480m": EmbeddingModel("giga-embeddings-480m-0826", _GIGA),
    "qwen3-emb-4b": EmbeddingModel("qwen3-embedding-4b", _QWEN),
    "bge-m3": EmbeddingModel("bge-m3", None),
}


async def embed_texts(
    client: httpx.AsyncClient,
    model: EmbeddingModel,
    texts: Sequence[str],
    *,
    query: bool,
    batch: int,
) -> list[np.ndarray]:
    """Векторы текстов в том же порядке; запросам — префикс модели."""
    inputs = [
        model.query_template.format(text=t) if query and model.query_template else t for t in texts
    ]
    vectors: list[np.ndarray] = []
    for start in range(0, len(inputs), batch):
        response = await client.post(
            "/v1/embeddings", json={"input": inputs[start : start + batch], "model": model.name}
        )
        response.raise_for_status()
        data = sorted(response.json()["data"], key=lambda item: item["index"])
        vectors.extend(np.asarray(item["embedding"], dtype=np.float32) for item in data)
    return vectors


async def rerank_scores(
    client: httpx.AsyncClient, query: str, documents: Sequence[str]
) -> list[float]:
    """Балл ранжировщика для каждого документа в том же порядке."""
    response = await client.post(
        "/v1/rerank", json={"query": query, "documents": list(documents), "top_n": len(documents)}
    )
    response.raise_for_status()
    scores = {item["index"]: float(item["relevance_score"]) for item in response.json()["results"]}
    return [scores[i] for i in range(len(documents))]


def vector_texts(
    docs: Mapping[str, Doc], *, contexts: Mapping[str, str], parts: bool, context: bool
) -> dict[str, list[str]]:
    """Тексты для эмбеддинга каждого фрагмента: целиком или по частям, с контекстом или без."""
    result = {}
    for key, doc in docs.items():
        ctx = contexts.get(key) if context else None
        pieces = (
            split_parts(doc.text, threshold=PART_THRESHOLD, limit=PART_LIMIT)
            if parts
            else [doc.text]
        )
        result[key] = [document_text(doc.title, doc.heading_path, p, ctx) for p in pieces]
    return result


def rerank_document(doc: Doc) -> str:
    """Текст фрагмента для ранжировщика: название, путь заголовков, текст."""
    return document_text(doc.title, doc.heading_path, doc.text, None)


def rerank_key(query: str, doc: Doc) -> str:
    return f"{text_key('query', query)}|{text_key('doc', rerank_document(doc))}"


class CacheSources:
    """Выдачи поисков и баллы ранжировщика из кэша для `retrieval.Engine`."""

    def __init__(
        self,
        *,
        docs: Mapping[str, Doc],
        cache_dir: Path,
        contexts: Mapping[str, str],
        full_text_rankings: Mapping[tuple[str, bool], list[str]],
        strict: bool,
    ) -> None:
        self._docs = docs
        self._dir = cache_dir
        self._contexts = contexts
        self._full_text = full_text_rankings
        self._strict = strict
        self._vectors: dict[str, VectorStore] = {}
        self._matrices: dict[tuple[str, bool, bool], dict[str, np.ndarray]] = {}
        self._scores: dict[str, JsonStore] = {}
        self.wanted: dict[str, set[tuple[str, str]]] = {}

    def _store(self, model: str) -> VectorStore:
        if model not in self._vectors:
            self._vectors[model] = VectorStore(self._dir / "embeddings" / f"{model}.npz")
        return self._vectors[model]

    def _matrix(self, config: Config) -> dict[str, np.ndarray]:
        marker = (config.embeddings, config.parts, config.context)
        if marker not in self._matrices:
            store = self._store(config.embeddings)
            texts = vector_texts(
                self._docs, contexts=self._contexts, parts=config.parts, context=config.context
            )
            self._matrices[marker] = {
                key: np.stack([store.get(text_key("doc", t)) for t in pieces])
                for key, pieces in texts.items()
            }
        return self._matrices[marker]

    def vector(self, config: Config, query: str) -> list[str]:
        vector = self._store(config.embeddings).get(text_key("query", query))
        return cosine_ranking(vector, self._matrix(config), limit=CANDIDATES)

    def full_text(self, config: Config, query: str) -> list[str]:
        return self._full_text[(query, config.context)]

    def rerank(self, model: str, query: str, keys: Sequence[str]) -> dict[str, float]:
        if model not in self._scores:
            self._scores[model] = JsonStore(self._dir / "rerank" / f"{model}.json")
        store = self._scores[model]
        scores = {}
        for key in keys:
            cache_key = rerank_key(query, self._docs[key])
            if store.missing([cache_key]):
                if self._strict:
                    raise KeyError(f"{model}: нет балла ранжировщика для {key}")
                self.wanted.setdefault(model, set()).add((query, key))
                scores[key] = 0.0
            else:
                scores[key] = float(store.get(cache_key))
        return scores


async def load_docs(database_url: str) -> dict[str, Doc]:
    """Фрагменты действующих документов индекса."""
    engine = make_engine(database_url)
    try:
        async with make_sessionmaker(engine)() as session:
            fragments = await service.list_fragments(session)
    finally:
        await engine.dispose()
    return {
        f.key: Doc(
            key=f.key,
            source_id=f.document.source_id,
            title=f.document.title,
            heading_path=f.heading_path,
            text=f.text,
            fragment_id=str(f.id),
        )
        for f in fragments
    }


_FULL_TEXT = """
SELECT key FROM tune_docs
WHERE ctx = $2 AND tsv @@ replace(plainto_tsquery('russian', $1)::text, ' & ', ' | ')::tsquery
ORDER BY ts_rank_cd(tsv, replace(plainto_tsquery('russian', $1)::text, ' & ', ' | ')::tsquery)
  DESC, fragment_id
LIMIT 40
"""


async def full_text_rankings(
    database_url: str,
    docs: Mapping[str, Doc],
    contexts: Mapping[str, str],
    queries: Sequence[str],
) -> dict[tuple[str, bool], list[str]]:
    """Выдача полнотекстового поиска для каждого запроса без контекста и с контекстом.

    Тексты кладутся во временную таблицу с тем же выражением `tsvector`, что в бою:
    `heading_path || ' ' || text`, с контекстом — строка контекста перед ним.
    """
    conn = await asyncpg.connect(database_url.replace("+asyncpg", ""))
    try:
        await conn.execute(
            "CREATE TEMP TABLE tune_docs (key text, fragment_id uuid, ctx boolean, tsv tsvector)"
        )
        rows = []
        for key, doc in docs.items():
            base = f"{doc.heading_path} {doc.text}"
            rows.append((key, doc.fragment_id, False, base))
            ctx = contexts.get(key)
            rows.append((key, doc.fragment_id, True, f"{ctx} {base}" if ctx else base))
        await conn.executemany(
            "INSERT INTO tune_docs VALUES ($1, $2::uuid, $3, to_tsvector('russian', $4))", rows
        )
        result = {}
        for query in dict.fromkeys(queries):
            for ctx in (False, True):
                found = await conn.fetch(_FULL_TEXT, query, ctx)
                result[(query, ctx)] = [row["key"] for row in found]
        return result
    finally:
        await conn.close()
