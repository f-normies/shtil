"""Гибридный поиск по фрагментам `active` документов (спека #27, раздел 6).

Векторный поиск и полнотекстовый дают по 40 лучших. Слияние — RRF в Python: балл фрагмента
`Σ 1/(60 + место)` по обоим спискам. Корпус — сотни фрагментов, слияние в коде проще проверить.

Ранжировщик (спека #70, раздел 7) пересортировывает первые `RERANK_DEPTH` после слияния
по своему баллу, остальные идут следом в порядке RRF. Узлы ранжировщика недоступны или
ответили ошибкой — остаётся порядок RRF и предупреждение в логе: врач получает ответ без
ранжирования. Чтение из БД (`search`) и ранжирование (`reranked`) разделены: вызов узла
идёт до `rerank_timeout_seconds`, соединение с БД на это время возвращается в пул.

Подбор #92 (спека #92, 5.1 и 5.5): поиск по нескольким запросам (`search_many`) сливает
выдачи запросов тем же RRF по местам (`fuse_queries`). Порог балла ранжировщика
(`min_score`) убирает фрагменты ниже него; фрагменты дальше глубины ранжировщика баллов
не имеют и при пороге не показываются.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

import structlog
from pgvector.sqlalchemy import HALFVEC
from pydantic_ai import Embedder
from sqlalchemy import ColumnElement, Float, Text, cast, func, literal, literal_column, select, text
from sqlalchemy.dialects.postgresql import TSQUERY
from sqlalchemy.ext.asyncio import AsyncSession

from clin_compass.core.ai.nodes import AiError
from clin_compass.core.ai.runs import describe_error
from clin_compass.modules.knowledge import models
from clin_compass.modules.knowledge.schemas import DocumentRef, Fragment, Hit

# Сколько лучших фрагментов берёт каждый поиск до слияния.
CANDIDATES = 40
# Константа RRF: сглаживает разницу между первыми местами.
RRF_K = 60
# Сколько первых после слияния получает ранжировщик. Подбор #70 (eval/rag/results/tune-*):
# 20 и 40 равны по качеству, 20 вдвое быстрее.
RERANK_DEPTH = 20
# Пара «вопрос — фрагмент» длиннее пакета llama-server (-ub 4096 токенов) — ошибка всего запроса.
# Самый длинный фрагмент сейчас — 9,4 тыс. знаков (~3 тыс. токенов); 10 тыс. знаков с запасом
# влезают в пакет.
RERANK_CHARS = 10_000
RERANK_TASK = "knowledge.search"

log = structlog.get_logger()


class Reranker(Protocol):
    """Баллы документов для запроса в их порядке: `core.ai.rerank.Reranker`."""

    async def rerank(self, query: str, documents: Sequence[str], *, task: str) -> list[float]:
        """Балл каждого документа."""
        ...


_RUSSIAN: ColumnElement[str] = literal_column("'russian'::regconfig")


@dataclass(frozen=True)
class Fused:
    """Фрагмент после слияния: балл RRF и места в обоих поисках (с 1, None — не найден)."""

    id: UUID
    score: float
    vector_rank: int | None
    text_rank: int | None


def fuse(vector: Sequence[UUID], text: Sequence[UUID], *, k: int = RRF_K) -> list[Fused]:
    """Сливает два ранжированных списка по RRF; лучший — первый.

    При равном балле выше фрагмент с лучшим местом, затем найденный векторным поиском:
    порядок не зависит от порядка словаря.
    """
    ranks: dict[UUID, tuple[int | None, int | None]] = {}
    for rank, fragment_id in enumerate(vector, start=1):
        ranks[fragment_id] = (rank, None)
    for rank, fragment_id in enumerate(text, start=1):
        ranks[fragment_id] = (ranks.get(fragment_id, (None, None))[0], rank)
    fused = [
        Fused(
            id=fragment_id,
            score=sum(1 / (k + r) for r in (v, t) if r is not None),
            vector_rank=v,
            text_rank=t,
        )
        for fragment_id, (v, t) in ranks.items()
    ]
    return sorted(fused, key=_order)


def _order(item: Fused) -> tuple[float, int, bool]:
    best = min(r for r in (item.vector_rank, item.text_rank) if r is not None)
    return -item.score, best, item.vector_rank is None


def fuse_queries(rankings: Sequence[Sequence[UUID]], *, k: int = RRF_K) -> list[UUID]:
    """Слияние выдач нескольких запросов по RRF; лучший — первый.

    При равном балле выше фрагмент с лучшим местом, затем — найденный более ранним запросом.
    """
    return list(_fused_queries(rankings, k))


def _fused_queries(rankings: Sequence[Sequence[UUID]], k: int) -> dict[UUID, float]:
    """Балл RRF каждого фрагмента в порядке слияния."""
    scores: dict[UUID, float] = {}
    best: dict[UUID, tuple[int, int]] = {}
    for number, ranking in enumerate(rankings):
        for rank, fragment_id in enumerate(ranking, start=1):
            scores[fragment_id] = scores.get(fragment_id, 0.0) + 1 / (k + rank)
            best[fragment_id] = min(best.get(fragment_id, (rank, number)), (rank, number))
    return {i: scores[i] for i in sorted(scores, key=lambda i: (-scores[i], *best[i]))}


async def search(
    session: AsyncSession,
    embedder: Embedder,
    query: str,
    *,
    k: int,
    source_ids: Sequence[str] | None,
    rerank: bool = False,
) -> list[Hit]:
    """`k` лучших фрагментов по RRF; для ранжировщика (`rerank`) — не меньше `RERANK_DEPTH`."""
    embedded = await embedder.embed_query(query)
    nearest = await _nearest(session, list(embedded.embeddings[0]), source_ids)
    matching = await _matching(session, query, source_ids)
    fused = fuse(nearest, matching)[: max(k, RERANK_DEPTH) if rerank else k]
    fragments = await load_fragments(session, [f.id for f in fused])
    return [
        Hit(
            fragment=fragments[f.id],
            score=f.score,
            vector_rank=f.vector_rank,
            text_rank=f.text_rank,
        )
        for f in fused
    ]


async def search_many(
    session: AsyncSession,
    embedder: Embedder,
    queries: Sequence[str],
    *,
    k: int,
    source_ids: Sequence[str] | None,
    rerank: bool = False,
) -> list[Hit]:
    """Поиск по каждому запросу и слияние выдач `fuse_queries`; один запрос — `search`.

    Балл фрагмента — балл слияния, места в векторном и полнотекстовом поиске — по первому
    запросу, где фрагмент нашёлся.
    """
    if len(queries) == 1:
        return await search(
            session, embedder, queries[0], k=k, source_ids=source_ids, rerank=rerank
        )
    per_query = [
        await search(session, embedder, query, k=2 * CANDIDATES, source_ids=source_ids)
        for query in queries
    ]
    first: dict[UUID, Hit] = {}
    for hits in per_query:
        for hit in hits:
            first.setdefault(hit.fragment.id, hit)
    fused = _fused_queries([[hit.fragment.id for hit in hits] for hits in per_query], RRF_K)
    top = list(fused)[: max(k, RERANK_DEPTH) if rerank else k]
    return [first[i].model_copy(update={"score": fused[i]}) for i in top]


async def reranked(
    reranker: Reranker,
    query: str,
    hits: Sequence[Hit],
    *,
    k: int,
    min_score: float | None = None,
) -> list[Hit]:
    """`k` лучших: первые `RERANK_DEPTH` по баллу ранжировщика, за ними остальные по RRF.

    При равном балле выше фрагмент с лучшим местом RRF. С порогом `min_score` — только
    фрагменты с баллом не ниже него. Отказ ранжировщика — порядок RRF без порога.
    Вызывать без открытой транзакции.
    """
    head, tail = list(hits[:RERANK_DEPTH]), list(hits[RERANK_DEPTH:])
    documents = [_rerank_text(hit.fragment) for hit in head]
    try:
        scores = await reranker.rerank(query, documents, task=RERANK_TASK)
    except AiError as exc:
        # Без ранжирования поиск хуже, но ответ врач получит.
        log.warning("knowledge.rerank_failed", error=describe_error(exc))
        return list(hits[:k])
    order = sorted(range(len(head)), key=lambda i: (-scores[i], i))
    scored = [head[i].model_copy(update={"rerank_score": scores[i]}) for i in order]
    if min_score is not None:
        return [hit for hit in scored if hit.rerank_score >= min_score][:k]  # type: ignore[operator]
    return (scored + tail)[:k]


def _rerank_text(fragment: Fragment) -> str:
    """Текст фрагмента для ранжировщика: как в подборе — название, путь заголовков, текст."""
    return f"{fragment.document.title}. {fragment.heading_path}\n{fragment.text}"[:RERANK_CHARS]


async def _nearest(
    session: AsyncSession, embedding: list[float], source_ids: Sequence[str] | None
) -> list[UUID]:
    """40 ближайших по косинусному расстоянию фрагментов `active` документов.

    Без итеративного обхода HNSW отдаёт `hnsw.ef_search` (40) кандидатов до фильтра, и
    фрагменты прежних версий — те же тексты с теми же векторами — вытеснили бы половину
    действующих. `relaxed_order` слегка нарушает порядок, поэтому результат сортируется заново.
    """
    await session.execute(text("SET LOCAL hnsw.iterative_scan = relaxed_order"))
    query = literal(embedding, HALFVEC(models.EMBEDDING_DIMENSIONS))
    distance = models.Fragment.embedding.op("<=>", return_type=Float)(query)
    nearest = (
        select(models.Fragment.id, distance.label("distance"))
        .join(models.Document, models.Document.id == models.Fragment.document_id)
        .where(*_active(source_ids))
        .order_by(distance)
        .limit(CANDIDATES)
        .cte("nearest")
        .prefix_with("MATERIALIZED")
    )
    rows = await session.scalars(select(nearest.c.id).order_by(nearest.c.distance, nearest.c.id))
    return list(rows)


async def _matching(
    session: AsyncSession, query: str, source_ids: Sequence[str] | None
) -> list[UUID]:
    """40 лучших по `ts_rank_cd` фрагментов, где есть хоть одна лемма вопроса.

    `plainto_tsquery` соединяет леммы через «И»: вопрос врача почти никогда не совпал бы
    с фрагментом целиком. Здесь леммы соединены через «ИЛИ». Вопрос из одних стоп-слов даёт
    пустой запрос, он не совпадает ни с чем: остаётся только векторный поиск.
    """
    lemmas = cast(func.plainto_tsquery(_RUSSIAN, literal(query, Text)), Text)
    any_lemma = cast(func.replace(lemmas, " & ", " | "), TSQUERY)
    rank = func.ts_rank_cd(models.Fragment.tsv, any_lemma)
    rows = await session.scalars(
        select(models.Fragment.id)
        .join(models.Document, models.Document.id == models.Fragment.document_id)
        .where(*_active(source_ids), models.Fragment.tsv.bool_op("@@")(any_lemma))
        .order_by(rank.desc(), models.Fragment.id)
        .limit(CANDIDATES)
    )
    return list(rows)


def _active(source_ids: Sequence[str] | None) -> list[ColumnElement[bool]]:
    clauses = [models.Document.status == "active"]
    if source_ids is not None:
        clauses.append(models.Document.source_id.in_(source_ids))
    return clauses


async def load_fragments(session: AsyncSession, ids: Sequence[UUID]) -> dict[UUID, Fragment]:
    """Фрагменты с документами по id; векторы и tsvector не читаются."""
    rows = await session.execute(
        select(models.Fragment, models.Document)
        .join(models.Document, models.Document.id == models.Fragment.document_id)
        .where(models.Fragment.id.in_(ids))
    )
    return {fragment.id: to_schema(fragment, document) for fragment, document in rows}


def to_schema(fragment: models.Fragment, document: models.Document) -> Fragment:
    """Строки таблиц → публичная схема фрагмента."""
    return Fragment.model_validate(
        {
            "id": fragment.id,
            "key": fragment.key,
            "document": DocumentRef.model_validate(
                {
                    "id": document.id,
                    "source_id": document.source_id,
                    "title": document.title,
                    "version": document.version,
                    "url": document.url,
                    "kind": document.kind,
                    "status": document.status,
                }
            ),
            "section": fragment.section,
            "section_title": fragment.section_title,
            "heading_path": fragment.heading_path,
            "kind": fragment.kind,
            "text": fragment.text,
            "uur": fragment.uur,
            "udd": fragment.udd,
        }
    )
