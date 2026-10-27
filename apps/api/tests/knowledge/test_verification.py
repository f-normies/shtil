"""Проверка утверждений ответа вторым проходом LLM (спека #92, 5.3).

LLM — `FunctionModel`: первый ответ — утверждения, второй — вердикты проверки.
"""

from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.core.ai.llm import Llm
from clin_compass.modules.knowledge import service
from clin_compass.modules.knowledge.schemas import Answer
from clin_compass.modules.knowledge.verification import TASK
from tests.core.fake_nodes import MemoryRecorder
from tests.freetext.helpers import Script
from tests.knowledge.helpers import Piece, add_document, words_embedder

QUESTION = "Как принимать маритупирдин и сколько длится курс?"
CONTEXT = "Ж, 61 год. СКФ 52 мл/мин."
DOSING = Piece(
    "Принимать по 20 мг один раз в сутки. Приём препарата возможен независимо от еды. "
    "Курс — 4 недели.",
    section="4.2",
)
CLAIMS = {
    "claims": [
        {"statement": "Принимают по 20 мг раз в сутки.", "fragment": "F1", "first": 1, "last": 1},
        {"statement": "Курс — 4 недели.", "fragment": "F1", "first": 3, "last": 3},
    ]
}


async def _answer(
    sessionmaker: async_sessionmaker[AsyncSession],
    script: Script,
    recorder: MemoryRecorder | None = None,
    **options: Any,
) -> Answer:
    async with sessionmaker() as session:
        await add_document(session, "aviandr-ohlp", [DOSING], kind="drug_label", title="ОХЛП")
        await session.commit()
    embedder, _ = words_embedder()
    llm = Llm(script.model, recorder or MemoryRecorder())
    ai = service.KnowledgeAi(embedder, llm, **options)
    return await service.answer(sessionmaker, ai, QUESTION, context=CONTEXT)


async def test_claims_rejected_by_verifier_are_dropped(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    recorder = MemoryRecorder()
    script = Script(CLAIMS, {"C1": True, "C2": False})

    result = await _answer(sessionmaker, script, recorder, verify=True)

    assert [c.statement for c in result.claims] == ["Принимают по 20 мг раз в сутки."]
    assert result.dropped == 1
    assert [run.task for run in recorder.runs] == ["knowledge.answer", TASK]


async def test_verifier_sees_patient_claims_and_quotes(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    script = Script(CLAIMS, {"C1": True, "C2": True})

    await _answer(sessionmaker, script, verify=True)

    verify = script.calls[1]
    assert CONTEXT in verify.prompt
    assert "[C2] Утверждение: Курс — 4 недели." in verify.prompt
    assert "Цитата: Курс — 4 недели." in verify.prompt
    assert set(verify.schema["properties"]) == {"C1", "C2"}


async def test_verifier_failure_keeps_claims_and_warns(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    script = Script(CLAIMS, {"C1": "может быть"})

    with structlog.testing.capture_logs() as logs:
        result = await _answer(sessionmaker, script, verify=True)

    assert len(result.claims) == 2
    assert result.dropped == 0
    [warning] = [entry for entry in logs if entry["event"] == "knowledge.verify_failed"]
    assert "маритупирдин" not in repr(warning)


async def test_empty_answer_needs_no_verification(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    script = Script({"claims": []}, {"C1": True})

    result = await _answer(sessionmaker, script, verify=True)

    assert result.claims == []
    assert len(script.calls) == 1


async def test_verification_is_off_by_default(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    script = Script(CLAIMS)

    result = await _answer(sessionmaker, script)

    assert len(result.claims) == 2
    assert len(script.calls) == 1
