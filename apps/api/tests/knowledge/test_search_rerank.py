"""Поиск с ранжировщиком (спека #70, раздел 7): порядок, текст документов, отказ узлов."""

from collections.abc import Sequence

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.core.ai.nodes import AiError, NodesUnavailableError
from clin_compass.modules.knowledge import service
from clin_compass.modules.knowledge.search import RERANK_CHARS, RERANK_DEPTH
from tests.knowledge.helpers import Piece, add_document, words_embedder

QUESTION = "снотворное при бессоннице"
# RRF ставит первым фрагмент со всеми словами вопроса.
LEXICAL = Piece("Снотворное при бессоннице назначают коротким курсом.", heading_path="Лечение")
ANSWER = Piece("При нарушениях сна рекомендуется когнитивно-поведенческая терапия.")


class FakeReranker:
    """Балл — по слову во фрагменте; запоминает, что пришло."""

    def __init__(self, scores: dict[str, float], error: AiError | None = None) -> None:
        self._scores = scores
        self._error = error
        self.calls: list[tuple[str, list[str], str]] = []

    async def rerank(self, query: str, documents: Sequence[str], *, task: str) -> list[float]:
        self.calls.append((query, list(documents), task))
        if self._error is not None:
            raise self._error
        return [next((s for word, s in self._scores.items() if word in d), 0.0) for d in documents]


async def _search(
    sessionmaker: async_sessionmaker[AsyncSession], reranker: FakeReranker | None, k: int = 10
) -> list[str]:
    embedder, _ = words_embedder()
    async with sessionmaker() as session:
        await add_document(session, "kr-test", [LEXICAL, ANSWER], title="КР по бессоннице")
        hits = await service.search(session, embedder, QUESTION, k=k, reranker=reranker)
    return [h.fragment.text for h in hits]


async def test_without_reranker_order_is_rrf(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    assert (await _search(sessionmaker, None))[0] == LEXICAL.text


async def test_reranker_reorders_hits(sessionmaker: async_sessionmaker[AsyncSession]) -> None:
    reranker = FakeReranker({"когнитивно": 5.0, "Снотворное": -1.0})

    texts = await _search(sessionmaker, reranker)

    assert texts[:2] == [ANSWER.text, LEXICAL.text]


async def test_reranker_sees_title_heading_and_text(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    reranker = FakeReranker({})

    await _search(sessionmaker, reranker)

    [(query, documents, task)] = reranker.calls
    assert (query, task) == (QUESTION, "knowledge.search")
    assert f"КР по бессоннице. Лечение\n{LEXICAL.text}" in documents


async def test_reranker_gets_at_most_depth_candidates_and_k_is_kept(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    reranker = FakeReranker({"когнитивно": 5.0})

    texts = await _search(sessionmaker, reranker, k=1)

    assert texts == [ANSWER.text]
    assert len(reranker.calls[0][1]) <= RERANK_DEPTH


async def test_long_fragment_is_cut_for_reranker(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    # Пара длиннее пакета llama-server (-ub 4096) роняет ранжирование всего вопроса.
    embedder, _ = words_embedder()
    long = Piece("Снотворное при бессоннице. " + "Длинный текст раздела. " * 1000)
    reranker = FakeReranker({})
    async with sessionmaker() as session:
        await add_document(session, "kr-test", [long], title="КР по бессоннице")
        await service.search(session, embedder, QUESTION, k=1, reranker=reranker)

    [document] = reranker.calls[0][1]
    assert len(document) == RERANK_CHARS
    assert document.startswith("КР по бессоннице. Лечение\nСнотворное при бессоннице.")


async def test_k_beyond_depth_keeps_rrf_tail_after_reranked_head(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    embedder, _ = words_embedder()
    pieces = [Piece(f"Бессонница, вариант {n}.", section=f"4.{n}") for n in range(1, 26)]
    reranker = FakeReranker({})
    async with sessionmaker() as session:
        await add_document(session, "kr-test", pieces, title="КР по бессоннице")
        hits = await service.search(session, embedder, "бессонница", k=25, reranker=reranker)

    assert len(reranker.calls[0][1]) == RERANK_DEPTH
    assert len(hits) == 25


async def test_reranker_failure_keeps_rrf_order(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    reranker = FakeReranker({}, error=NodesUnavailableError("rerank"))

    with structlog.testing.capture_logs() as logs:
        texts = await _search(sessionmaker, reranker)

    assert texts[0] == LEXICAL.text
    assert any(
        log["event"] == "knowledge.rerank_failed" and log["log_level"] == "warning" for log in logs
    )


def test_build_ai_adds_reranker_only_when_nodes_are_set() -> None:
    from clin_compass.core.ai.rerank import Reranker
    from clin_compass.core.config import Settings
    from tests.core.fake_nodes import MemoryRecorder

    base = {
        "database_url": "postgresql+asyncpg://unused/none",
        "environment": "test",
        "llm_urls": ("http://127.0.0.1:9",),
        "embeddings_urls": ("http://127.0.0.1:9",),
    }

    without = service.build_ai(Settings.model_validate(base), MemoryRecorder())
    with_nodes = service.build_ai(
        Settings.model_validate({**base, "rerank_urls": ("http://127.0.0.1:9",)}), MemoryRecorder()
    )

    assert without.reranker is None
    assert isinstance(with_nodes.reranker, Reranker)


def _startup_events(**urls: tuple[str, ...]) -> list[str]:
    """События лога `build_ai_if_configured`: его вызывает старт api."""
    from clin_compass.core.config import Settings
    from tests.core.fake_nodes import MemoryRecorder

    settings = Settings.model_validate(
        {
            "database_url": "postgresql+asyncpg://unused/none",
            "environment": "test",
            "llm_urls": ("http://127.0.0.1:9",),
            "embeddings_urls": ("http://127.0.0.1:9",),
            "rerank_urls": (),
            **urls,
        }
    )
    with structlog.testing.capture_logs() as logs:
        service.build_ai_if_configured(settings, MemoryRecorder())
    return [f"{log['log_level']}:{log['event']}" for log in logs]


def test_startup_without_reranker_nodes_warns() -> None:
    assert "warning:knowledge.rerank_disabled" in _startup_events()


def test_startup_with_reranker_nodes_does_not_warn() -> None:
    events = _startup_events(rerank_urls=("http://127.0.0.1:9",))

    assert "warning:knowledge.rerank_disabled" not in events
