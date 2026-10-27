"""Переписывание вопроса, поиск по нескольким запросам и порог ранжировщика (спека #92, 5.1, 5.5).

LLM — `FunctionModel`: первый ответ — запросы переписывания, второй — утверждения ответа.
"""

from typing import Any
from uuid import uuid4

import pytest
import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.core.ai.llm import Llm
from clin_compass.core.ai.nodes import NodesUnavailableError
from clin_compass.modules.knowledge import search as search_module
from clin_compass.modules.knowledge import service
from clin_compass.modules.knowledge.schemas import Answer, DocumentRef, Fragment, Hit
from clin_compass.modules.knowledge.search import CANDIDATES, fuse_queries
from tests.core.fake_nodes import MemoryRecorder
from tests.freetext.helpers import Script
from tests.knowledge.helpers import Piece, add_document, words_embedder
from tests.knowledge.test_search_rerank import ANSWER, LEXICAL, FakeReranker

QUESTION = "чем лечить что не спит"
CONTEXT = "М, 70 лет. Бессонница два месяца."
REWRITE = "когнитивно-поведенческая терапия бессонницы"
EMPTY: dict[str, Any] = {"claims": []}


def _hit(text: str) -> Hit:
    document = DocumentRef(
        id=uuid4(),
        source_id="kr-test",
        title="КР",
        version="1",
        url="https://cr.minzdrav.gov.ru/view-cr/1_1",
        kind="clinical_guideline",
        status="active",
    )
    fragment = Fragment(
        id=uuid4(),
        key=f"kr-test/doc_3/{text}",
        document=document,
        section="doc_3",
        section_title="Лечение",
        heading_path="Лечение",
        kind="text",
        text=text,
        uur=None,
        udd=None,
    )
    return Hit(fragment=fragment, score=0.0, vector_rank=1, text_rank=None)


def test_fuse_queries_sums_reciprocal_ranks_across_lists() -> None:
    a, b, c, d = (uuid4() for _ in range(4))

    assert fuse_queries([[a, b, c], [b, d]]) == [b, a, d, c]


def test_fuse_queries_breaks_ties_by_best_rank_then_first_list() -> None:
    a, b = uuid4(), uuid4()

    assert fuse_queries([[a], [b]]) == [a, b]


async def _answer(
    sessionmaker: async_sessionmaker[AsyncSession],
    script: Script,
    reranker: FakeReranker | None,
    *,
    recorder: MemoryRecorder | None = None,
    context: str | None = None,
    pieces: list[Piece] | None = None,
    **options: Any,
) -> Answer:
    async with sessionmaker() as session:
        await add_document(session, "kr-test", pieces or [LEXICAL, ANSWER], title="КР")
        await session.commit()
    embedder, _ = words_embedder()
    ai = service.KnowledgeAi(
        embedder, Llm(script.model, recorder or MemoryRecorder()), reranker=reranker, **options
    )
    return await service.answer(sessionmaker, ai, QUESTION, context=context)


async def test_rewritten_query_is_searched_and_used_for_reranking(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    recorder = MemoryRecorder()
    script = Script({"queries": [REWRITE]}, EMPTY)
    reranker = FakeReranker({"когнитивно": 5.0})

    await _answer(sessionmaker, script, reranker, recorder=recorder, rewrite="one")

    assert [run.task for run in recorder.runs] == ["knowledge.rewrite", "knowledge.answer"]
    assert reranker.calls[0][0] == REWRITE
    assert "[F1] КР" in script.calls[1].prompt and ANSWER.text in script.calls[1].prompt


async def test_rewrite_sees_question_and_patient_and_limits_queries(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    script = Script({"queries": [REWRITE, "бессонница у пожилых"]}, EMPTY)

    await _answer(sessionmaker, script, None, context=CONTEXT, rewrite="multi")

    rewrite = script.calls[0]
    assert QUESTION in rewrite.prompt and CONTEXT in rewrite.prompt
    assert rewrite.schema["properties"]["queries"]["maxItems"] == 3


async def test_rewrite_failure_falls_back_to_question(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    script = Script(NodesUnavailableError("llm"), EMPTY)
    reranker = FakeReranker({})

    with structlog.testing.capture_logs() as logs:
        await _answer(sessionmaker, script, reranker, rewrite="one")

    assert reranker.calls[0][0] == QUESTION
    assert any(entry["event"] == "knowledge.rewrite_failed" for entry in logs)


async def test_patient_mode_searches_question_with_context_without_llm(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    script = Script(EMPTY)
    reranker = FakeReranker({})

    await _answer(sessionmaker, script, reranker, context=CONTEXT, rewrite="patient")

    assert len(script.calls) == 1
    assert reranker.calls[0][0] == f"{QUESTION}\n{CONTEXT}"


async def test_fragments_below_threshold_are_not_shown(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    script = Script(EMPTY)
    reranker = FakeReranker({"когнитивно": 5.0, "Снотворное": -3.0})

    await _answer(sessionmaker, script, reranker, min_score=0.0)

    [call] = script.calls
    assert ANSWER.text in call.prompt and LEXICAL.text not in call.prompt


async def test_nothing_above_threshold_gives_empty_answer_without_llm(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    script = Script(EMPTY)
    reranker = FakeReranker({"когнитивно": -5.0, "Снотворное": -3.0})

    result = await _answer(sessionmaker, script, reranker, min_score=0.0)

    assert (result.claims, result.run_id) == ([], None)
    assert script.calls == []


async def test_threshold_is_ignored_when_reranker_fails(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    script = Script(EMPTY)
    reranker = FakeReranker({}, error=NodesUnavailableError("rerank"))

    await _answer(sessionmaker, script, reranker, min_score=100.0)

    [call] = script.calls
    assert LEXICAL.text in call.prompt


async def test_rewrite_equal_to_question_is_not_searched_twice(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    script = Script({"queries": [f" {QUESTION.upper()} "]}, EMPTY)
    reranker = FakeReranker({})

    await _answer(sessionmaker, script, reranker, rewrite="one")

    assert reranker.calls[0][0] == QUESTION


async def test_search_many_searches_each_query_and_fuses_by_rank(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    a, b, c = (_hit(text) for text in ("а", "б", "в"))
    per_query = {"вопрос": [a, b], "запрос": [c, b]}
    calls: list[tuple[str, int]] = []

    async def fake_search(
        session: object, embedder: object, query: str, *, k: int, **_: object
    ) -> list[Hit]:
        calls.append((query, k))
        return per_query[query]

    monkeypatch.setattr(search_module, "search", fake_search)

    hits = await search_module.search_many(
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        ["вопрос", "запрос"],
        k=2,
        source_ids=None,
    )

    assert calls == [("вопрос", 2 * CANDIDATES), ("запрос", 2 * CANDIDATES)]
    assert [h.fragment.text for h in hits] == ["б", "а"]
    assert hits[0].score == pytest.approx(1 / 62 + 1 / 62)


async def test_rerank_by_question_keeps_rewrite_for_search_only(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    script = Script({"queries": [REWRITE]}, EMPTY)
    reranker = FakeReranker({"когнитивно": 5.0})

    await _answer(sessionmaker, script, reranker, rewrite="one", rerank_query="question")

    assert reranker.calls[0][0] == QUESTION
    assert ANSWER.text in script.calls[1].prompt
