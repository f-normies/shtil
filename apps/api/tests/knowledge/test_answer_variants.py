"""Варианты ответа для подбора #92 (спека #92, 5.3–5.4): правила для ловушек и рассуждения.

LLM — `FunctionModel` с заданным ответом. Параметры запроса видны в записи ai_runs.
"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.core.ai.llm import Llm
from clin_compass.modules.knowledge import service
from clin_compass.modules.knowledge.answering import THINKING_MAX_TOKENS
from clin_compass.modules.knowledge.schemas import Answer
from tests.core.fake_nodes import MemoryRecorder
from tests.freetext.helpers import Script
from tests.knowledge.helpers import Piece, add_document, words_embedder

QUESTION = "авиандр 40 мг 2 р/д можно?"
DOSING = Piece("Внутрь по 1 таблетке (20 мг) 2 раза в сутки.", section="4.2")


def _ai(script: Script, recorder: MemoryRecorder, **options: Any) -> service.KnowledgeAi:
    embedder, _ = words_embedder()
    return service.KnowledgeAi(embedder, Llm(script.model, recorder), **options)


async def _seed(sessionmaker: async_sessionmaker[AsyncSession]) -> None:
    async with sessionmaker() as session:
        await add_document(session, "aviandr-ohlp", [DOSING], kind="drug_label", title="ОХЛП")
        await session.commit()


async def _answer(
    sessionmaker: async_sessionmaker[AsyncSession],
    ai: service.KnowledgeAi,
    context: str | None = None,
    *,
    seed: bool = True,
) -> None:
    if seed:
        await _seed(sessionmaker)
    await service.answer(sessionmaker, ai, QUESTION, context=context)


def _thinking(recorder: MemoryRecorder) -> tuple[bool, int]:
    [run] = recorder.runs
    return run.params["extra_body"]["chat_template_kwargs"]["enable_thinking"], run.params[
        "max_tokens"
    ]


async def test_premise_rules_are_added_to_instructions(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    script = Script({"claims": []})

    await _answer(sessionmaker, _ai(script, MemoryRecorder(), premise=True))

    [call] = script.calls
    assert "неверное условие" in call.instructions


def test_premise_rules_change_prompt_version() -> None:
    script = Script({"claims": []})
    plain = _ai(script, MemoryRecorder()).prompt_version

    assert _ai(script, MemoryRecorder(), premise=True).prompt_version != plain


async def test_thinking_is_off_by_default(sessionmaker: async_sessionmaker[AsyncSession]) -> None:
    recorder = MemoryRecorder()

    await _answer(sessionmaker, _ai(Script({"claims": []}), recorder), context="Ж, 61 год.")

    assert _thinking(recorder) == (False, 4096)


async def test_thinking_always_raises_token_limit(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    recorder = MemoryRecorder()

    await _answer(sessionmaker, _ai(Script({"claims": []}), recorder, thinking="always"))

    assert _thinking(recorder) == (True, THINKING_MAX_TOKENS)


async def test_thinking_by_context_needs_patient_context(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    with_context = MemoryRecorder()
    without = MemoryRecorder()

    await _answer(
        sessionmaker, _ai(Script({"claims": []}), with_context, thinking="context"), "Ж, 61 год."
    )
    await _answer(
        sessionmaker, _ai(Script({"claims": []}), without, thinking="context"), seed=False
    )

    assert _thinking(with_context)[0] is True
    assert _thinking(without)[0] is False


async def test_aspects_come_first_in_schema_and_rules(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    script = Script({"aspects": ["доза"], "claims": []})

    result = await _answer_returning(sessionmaker, _ai(script, MemoryRecorder(), aspects=True))

    [call] = script.calls
    assert list(call.schema["properties"])[:2] == ["aspects", "claims"]
    assert call.schema["properties"]["aspects"]["maxItems"] == 5
    assert "aspects" in call.instructions
    assert result.claims == []


def test_aspects_change_prompt_version() -> None:
    script = Script({"claims": []})
    plain = _ai(script, MemoryRecorder()).prompt_version

    assert _ai(script, MemoryRecorder(), aspects=True).prompt_version != plain


async def _answer_returning(
    sessionmaker: async_sessionmaker[AsyncSession], ai: service.KnowledgeAi
) -> Answer:
    await _seed(sessionmaker)
    return await service.answer(sessionmaker, ai, QUESTION)
