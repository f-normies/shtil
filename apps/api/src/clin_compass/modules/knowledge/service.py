"""Публичный интерфейс модуля knowledge (спека #27, разделы 5–7).

Другие модули (#28) и eval вызывают только эти функции: поиск, чтение раздела, фрагмент
по id и ответ с проверкой цитат. CLI и health — индексацию, перенос индекса и его сводку
(спека #109). Инспектор источника — `view_source` по справочнику `SourceCatalog` (спека #106).
"""

from collections.abc import Sequence
from uuid import UUID

import structlog
from clin_compass_knowledge.loader import load_knowledge
from pydantic_ai import Embedder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.core.ai.embeddings import build_embedder
from clin_compass.core.ai.llm import Llm, build_llm_model
from clin_compass.core.ai.nodes import AiError, AiNotConfiguredError
from clin_compass.core.ai.rerank import build_reranker
from clin_compass.core.ai.runs import RunRecorder, describe_error
from clin_compass.core.config import Settings
from clin_compass.modules.knowledge import models, transfer
from clin_compass.modules.knowledge import search as hybrid
from clin_compass.modules.knowledge.answering import (
    CITATION,
    MAX_CLAIMS,
    MAX_FRAGMENTS,
    Answerer,
    Citation,
    Thinking,
)
from clin_compass.modules.knowledge.indexing import SourceReport, build_indexer, knowledge_root
from clin_compass.modules.knowledge.rewriting import (
    RerankQuery,
    Rewrite,
    Rewriter,
    distinct_rewrites,
)
from clin_compass.modules.knowledge.schemas import (
    Answer,
    Fragment,
    Hit,
    IndexedDocument,
    IndexSummary,
)
from clin_compass.modules.knowledge.search import Reranker
from clin_compass.modules.knowledge.sources import SourceCatalog, view_source
from clin_compass.modules.knowledge.transfer import ImportReport, IndexDump, TransferError
from clin_compass.modules.knowledge.verification import Verifier

log = structlog.get_logger()

# Сколько фрагментов отдаёт поиск по умолчанию; столько же видит модель в ответе.
DEFAULT_K = MAX_FRAGMENTS
# Задача ai_runs для эмбеддинга вопроса.
SEARCH_TASK = "knowledge.search"


class KnowledgeAi:
    """Клиенты моделей для интерактивных запросов: эмбеддинг вопроса и LLM ответа.

    `prompt_version` — версия промпта ответа: её пишет eval в результаты. Режим цитаты
    и лимиты по умолчанию — боевые; eval перебирает их (спека #70, 6.2).
    """

    def __init__(
        self,
        embedder: Embedder,
        llm: Llm,
        *,
        citation: Citation = CITATION,
        max_fragments: int = MAX_FRAGMENTS,
        max_claims: int = MAX_CLAIMS,
        reranker: Reranker | None = None,
        premise: bool = False,
        thinking: Thinking = "off",
        verify: bool = False,
        rewrite: Rewrite = "off",
        min_score: float | None = None,
        aspects: bool = False,
        rerank_query: RerankQuery = "rewrite",
    ) -> None:
        self.embedder = embedder
        self.reranker = reranker
        self.rewrite = rewrite
        self.rerank_query = rerank_query
        self.min_score = min_score
        self._verifier = Verifier(llm) if verify else None
        self._rewriter = Rewriter(llm, mode=rewrite) if rewrite in ("one", "multi") else None
        self._answerer = Answerer(
            llm,
            citation=citation,
            max_fragments=max_fragments,
            max_claims=max_claims,
            premise=premise,
            thinking=thinking,
            aspects=aspects,
        )
        self.max_fragments = max_fragments
        self.prompt_version = self._answerer.prompt_version

    async def queries(self, question: str, *, context: str | None = None) -> tuple[list[str], str]:
        """Запросы поиска и запрос для ранжировщика по режиму переписывания (спека #92, 5.1).

        Сбой переписывания — поиск по вопросу как есть и предупреждение в логе.
        """
        if self.rewrite == "patient" and context:
            joined = f"{question}\n{context}"
            return [joined], joined
        if self._rewriter is None:
            return [question], question
        try:
            rewrites = await self._rewriter.rewrite(question, context=context)
        except AiError as exc:
            log.warning("knowledge.rewrite_failed", error=describe_error(exc))
            return [question], question
        rewrites = distinct_rewrites(question, rewrites)
        if not rewrites:
            return [question], question
        rerank = question if self.rerank_query == "question" else rewrites[0]
        return [question, *rewrites], rerank

    async def answer(
        self, question: str, hits: Sequence[Hit], *, context: str | None = None
    ) -> Answer:
        """Ответ модели по уже найденным фрагментам; см. `answer`.

        С проверкой (`verify`) утверждения, которые проверка отклонила, врач не видит, они
        считаются в `dropped`. Сбой проверки — утверждения без неё и предупреждение в логе:
        они уже прошли проверку цитаты кодом.
        """
        result = await self._answerer.answer(question, hits, context=context)
        if self._verifier is None or not result.claims:
            return result
        try:
            verdicts = await self._verifier.check(question, result.claims, context=context)
        except AiError as exc:
            log.warning("knowledge.verify_failed", error=describe_error(exc))
            return result
        kept = [claim for claim, ok in zip(result.claims, verdicts, strict=True) if ok]
        rejected = len(result.claims) - len(kept)
        if rejected:
            log.info("knowledge.claims_rejected", rejected=rejected, run_id=str(result.run_id))
        return Answer(claims=kept, dropped=result.dropped + rejected, run_id=result.run_id)


def build_ai(settings: Settings, recorder: RunRecorder) -> KnowledgeAi:
    """Клиенты из узлов `COMPASS_EMBEDDINGS_URLS`, `COMPASS_LLM_URLS` и `COMPASS_RERANK_URLS`.

    Каждый вызов пишется в ai_runs: эмбеддинг вопроса и ранжирование — `knowledge.search`,
    ответ — `knowledge.answer`. Таймаут ответа — `llm_timeout_seconds`. Ранжировщик
    необязателен: без его адресов поиск идёт по порядку RRF.

    Raises:
        AiNotConfiguredError: Не заданы адреса узлов эмбеддингов или LLM.
    """
    return KnowledgeAi(
        build_embedder(settings, recorder, task=SEARCH_TASK),
        Llm(build_llm_model(settings), recorder),
        reranker=build_reranker(settings, recorder) if settings.rerank_urls else None,
    )


def build_ai_if_configured(settings: Settings, recorder: RunRecorder) -> KnowledgeAi | None:
    """`build_ai` для старта api: без узлов — None и предупреждение, остальное api работает.

    Без адресов ранжировщика — тоже предупреждение: поиск идёт по порядку RRF.
    """
    try:
        ai = build_ai(settings, recorder)
    except AiNotConfiguredError as exc:
        log.warning("knowledge.ai_disabled", role=exc.role)
        return None
    if ai.reranker is None:
        log.warning("knowledge.rerank_disabled")
    return ai


async def search(
    session: AsyncSession,
    embedder: Embedder,
    query: str,
    *,
    k: int = DEFAULT_K,
    source_ids: Sequence[str] | None = None,
    reranker: Reranker | None = None,
) -> list[Hit]:
    """Гибридный поиск по фрагментам действующих документов.

    Args:
        session: Сессия БД.
        embedder: Клиент эмбеддингов (`build_embedder(..., task="knowledge.search")`).
            Префикс инструкции к запросу ставит он сам.
        query: Вопрос или запрос врача.
        k: Сколько лучших фрагментов вернуть.
        source_ids: Только эти источники манифеста; None — все.
        reranker: Ранжировщик первых кандидатов; None — порядок RRF.

    Returns:
        До `k` фрагментов: по баллу ранжировщика, если он задан, иначе по баллу RRF.

    С ранжировщиком вызов узла идёт после чтения из БД в сессии `session`. Если у неё открыта
    транзакция, соединение занято на время вызова. `answer` ранжирует уже после закрытия сессии.

    Raises:
        NodesUnavailableError: Ни один узел эмбеддингов не принял запрос.
        AiServiceError, AiNoAnswerError: Узел эмбеддингов ответил ошибкой или не ответил.
    """
    hits = await hybrid.search(
        session, embedder, query, k=k, source_ids=source_ids, rerank=reranker is not None
    )
    if reranker is None:
        return hits
    return await hybrid.reranked(reranker, query, hits, k=k)


async def read_section(session: AsyncSession, source_id: str, section: str) -> list[Fragment]:
    """Фрагменты раздела действующего документа по порядку, без модели.

    Пример: `("aviandr-ohlp", "4.3")` — противопоказания из ОХЛП. Нет документа
    или раздела — пустой список.
    """
    rows = await session.execute(
        select(models.Fragment, models.Document)
        .join(models.Document, models.Document.id == models.Fragment.document_id)
        .where(
            models.Document.source_id == source_id,
            models.Document.status == "active",
            models.Fragment.section == section,
        )
        .order_by(models.Fragment.position)
    )
    return [hybrid.to_schema(fragment, document) for fragment, document in rows]


async def list_fragments(
    session: AsyncSession, *, source_ids: Sequence[str] | None = None
) -> list[Fragment]:
    """Все фрагменты действующих документов: по источнику, внутри — в порядке документа.

    Нужна оценке (`eval/rag/draft.py`): из фрагментов она готовит черновик вопросов.
    """
    query = (
        select(models.Fragment, models.Document)
        .join(models.Document, models.Document.id == models.Fragment.document_id)
        .where(models.Document.status == "active")
        .order_by(models.Document.source_id, models.Fragment.position)
    )
    if source_ids is not None:
        query = query.where(models.Document.source_id.in_(source_ids))
    rows = await session.execute(query)
    return [hybrid.to_schema(fragment, document) for fragment, document in rows]


async def list_documents(session: AsyncSession) -> list[IndexedDocument]:
    """Действующие версии документов индекса по источнику: версия и хеш исходника."""
    documents = await session.scalars(
        select(models.Document)
        .where(models.Document.status == "active")
        .order_by(models.Document.source_id)
    )
    return [
        IndexedDocument.model_validate(
            {
                "source_id": d.source_id,
                "version": d.version,
                "title": d.title,
                "kind": d.kind,
                "content_hash": d.content_hash,
            }
        )
        for d in documents
    ]


async def get_fragment(session: AsyncSession, fragment_id: UUID) -> Fragment | None:
    """Фрагмент по id, в том числе из прежней версии документа: на него может ссылаться
    подтверждённая рекомендация."""
    fragments = await hybrid.load_fragments(session, [fragment_id])
    return fragments.get(fragment_id)


async def answer(
    sessionmaker: async_sessionmaker[AsyncSession],
    ai: KnowledgeAi,
    question: str,
    *,
    context: str | None = None,
    source_ids: Sequence[str] | None = None,
) -> Answer:
    """Ответ на вопрос врача по базе знаний с проверенными цитатами.

    Поиск `ai.max_fragments` фрагментов, затем ответ модели по тем из них, что умещаются
    в бюджет контекста. Утверждение, цитаты которого нет в тексте фрагмента, отбрасывается
    и считается в `dropped`.

    Поиск идёт в своей короткой сессии. Она закрывается до вызовов ранжировщика и модели,
    и соединение возвращается в пул: ответ модели идёт до минуты, транзакция всё это время
    не висит.
    После ответа база не нужна, ссылки на фрагменты собираются из прочитанного при поиске.
    Вызывающий код тоже не держит свою транзакцию открытой на время вызова.

    Args:
        sessionmaker: Фабрика сессий БД для поиска.
        ai: Клиенты моделей (`build_ai`).
        question: Вопрос врача. Может содержать данные пациента: уходит только на узлы
            в tailnet, в логи и ai_runs попадает только хеш.
        context: Необязательные сведения о пациенте от вызывающего модуля (#28).
        source_ids: Только эти источники манифеста; None — все.

    Raises:
        NodesUnavailableError: Ни один узел эмбеддингов или LLM не принял запрос.
        AiServiceError, AiNoAnswerError: Узел ответил ошибкой или не ответил.
        AiOutputError: Модель ответила не по схеме.
    """
    queries, rerank_query = await ai.queries(question, context=context)
    async with sessionmaker() as session:
        hits = await hybrid.search_many(
            session,
            ai.embedder,
            queries,
            k=ai.max_fragments,
            source_ids=source_ids,
            rerank=ai.reranker is not None,
        )
    if ai.reranker is not None:
        hits = await hybrid.reranked(
            ai.reranker, rerank_query, hits, k=ai.max_fragments, min_score=ai.min_score
        )
    return await ai.answer(question, hits, context=context)


async def index_sources(
    settings: Settings, sessionmaker: async_sessionmaker[AsyncSession], *, force: bool = False
) -> list[SourceReport]:
    """Индексирует клинические рекомендации и инструкции из манифеста knowledge/.

    Источник с тем же хешем, что у действующего документа, пропускается, если не задан
    `force`. Ошибка источника — его строка в итоге, остальные источники индексируются.

    Raises:
        AiNotConfiguredError: Не заданы адреса узлов эмбеддингов.
        KnowledgeError: Данные knowledge/ не прошли проверку.
    """
    root = knowledge_root(settings)
    knowledge = load_knowledge(root)
    indexer = build_indexer(settings, sessionmaker, root)
    return await indexer.index(knowledge.manifest, force=force)


async def export_index(sessionmaker: async_sessionmaker[AsyncSession]) -> IndexDump:
    """Выгрузка действующих документов с фрагментами и векторами (`knowledge-export`).

    Raises:
        TransferError: В базе нет действующих документов.
    """
    return await transfer.export_index(sessionmaker)


async def import_index(
    sessionmaker: async_sessionmaker[AsyncSession], data: bytes
) -> list[ImportReport]:
    """Загрузка выгрузки одной транзакцией (`knowledge-import`); итог — строка на источник.

    Raises:
        TransferError: Файл не загружен, база не изменилась.
    """
    return await transfer.import_index(sessionmaker, data)


async def index_summary(sessionmaker: async_sessionmaker[AsyncSession]) -> IndexSummary:
    """Версия индекса и счётчики действующих документов и фрагментов: раздел health."""
    return await transfer.index_summary(sessionmaker)


__all__ = [
    "DEFAULT_K",
    "SEARCH_TASK",
    "ImportReport",
    "IndexDump",
    "KnowledgeAi",
    "SourceCatalog",
    "SourceReport",
    "TransferError",
    "answer",
    "build_ai",
    "build_ai_if_configured",
    "export_index",
    "get_fragment",
    "import_index",
    "index_sources",
    "index_summary",
    "list_documents",
    "list_fragments",
    "read_section",
    "search",
    "view_source",
]
