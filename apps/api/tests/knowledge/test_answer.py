"""Ответ с проверкой цитат (спека #27, раздел 7). LLM — `FunctionModel` с заданным ответом.

Режим дословной цитаты `quote`. Боевой режим номеров частей — `test_answer_sentences.py`.
"""

import re
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.core.ai.llm import Llm
from clin_compass.core.ai.nodes import AiOutputError
from clin_compass.core.config import Settings
from clin_compass.core.db import make_engine, make_sessionmaker
from clin_compass.modules.knowledge import service
from clin_compass.modules.knowledge.answering import MAX_CLAIMS
from clin_compass.modules.knowledge.schemas import Answer
from tests.core.fake_nodes import MemoryRecorder
from tests.freetext.helpers import Script
from tests.knowledge.helpers import Piece, add_document, probe_model, words_embedder

QUESTION = "Можно ли назначать маритупирдин при беременности?"
CONTRAINDICATION = Piece(
    "Противопоказания: беременность и период грудного вскармливания; возраст до 18 лет.",
    section="4.3",
    section_title="Противопоказания",
    heading_path="4.3. Противопоказания",
)
RECOMMENDATION = Piece(
    "Пациентам с депрессией рекомендуется назначение антидепрессантов при беременности "
    "только после оценки рисков.",
    kind="recommendation",
    uur="B",
    udd=2,
)


def _ai(script: Script, recorder: MemoryRecorder | None = None) -> service.KnowledgeAi:
    embedder, _ = words_embedder()
    return service.KnowledgeAi(
        embedder, Llm(script.model, recorder or MemoryRecorder()), citation="quote"
    )


def _claim(fragment: str, quote: str, statement: str = "Утверждение.") -> dict[str, str]:
    return {"statement": statement, "fragment": fragment, "quote": quote}


async def _answer(
    sessionmaker: async_sessionmaker[AsyncSession],
    script: Script,
    pieces: list[Piece],
    **kwargs: Any,
) -> Answer:
    async with sessionmaker() as session:
        await add_document(session, "aviandr-ohlp", pieces, kind="drug_label", title="ОХЛП")
        await session.commit()
    return await service.answer(sessionmaker, _ai(script), QUESTION, **kwargs)


async def test_claim_with_quote_missing_from_fragment_is_dropped(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    script = Script(
        {
            "claims": [
                _claim("F1", "«беременность и период грудного вскармливания;»", "Нельзя."),
                _claim("F1", "беременность является относительным противопоказанием"),
            ]
        }
    )
    recorder = MemoryRecorder()

    async with sessionmaker() as session:
        _, [fragment] = await add_document(
            session, "aviandr-ohlp", [CONTRAINDICATION], title="ОХЛП", version="0010"
        )
        await session.commit()
    result = await service.answer(sessionmaker, _ai(script, recorder), QUESTION)

    [claim] = result.claims
    assert claim.statement == "Нельзя."
    assert claim.quote == "беременность и период грудного вскармливания"
    assert claim.fragment.id == fragment.id
    assert claim.fragment.key == "aviandr-ohlp/4.3/1"
    assert (claim.fragment.source_id, claim.fragment.title) == ("aviandr-ohlp", "ОХЛП")
    assert (claim.fragment.version, claim.fragment.section) == ("0010", "4.3")
    assert claim.fragment.section_title == "Противопоказания"
    assert claim.fragment.status == "active"
    assert result.dropped == 1
    [run] = recorder.runs
    assert (run.task, run.status) == ("knowledge.answer", "ok")
    assert re.fullmatch(r"knowledge\.answer-[0-9a-f]{8}", run.prompt_version or "")


async def test_fragment_labels_are_limited_in_schema(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    script = Script({"claims": []})

    await _answer(sessionmaker, script, [CONTRAINDICATION, RECOMMENDATION])

    [call] = script.calls
    claims = call.schema["properties"]["claims"]
    assert claims["maxItems"] == MAX_CLAIMS
    item = claims["items"]
    if "$ref" in item:
        item = call.schema["$defs"][item["$ref"].split("/")[-1]]
    assert item["properties"]["fragment"]["enum"] == ["F1", "F2"]


async def test_label_outside_prompt_is_rejected(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    script = Script({"claims": [_claim("F3", "беременность")]})

    with pytest.raises(AiOutputError):
        await _answer(sessionmaker, script, [CONTRAINDICATION, RECOMMENDATION])


async def test_empty_answer_is_allowed(sessionmaker: async_sessionmaker[AsyncSession]) -> None:
    script = Script({"claims": []})

    result = await _answer(sessionmaker, script, [CONTRAINDICATION])

    assert (result.claims, result.dropped) == ([], 0)
    assert result.run_id is not None


async def test_empty_index_gives_empty_answer_without_model(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    script = Script({"claims": []})

    result = await service.answer(sessionmaker, _ai(script), QUESTION)

    assert result == Answer(claims=[], dropped=0, run_id=None)
    assert script.calls == []


async def test_prompt_shows_document_section_levels_and_context(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    script = Script({"claims": []})

    await _answer(
        sessionmaker,
        script,
        [RECOMMENDATION],
        context="Пациентка 30 лет, беременность 12 недель.",
    )

    [call] = script.calls
    assert QUESTION in call.prompt
    assert "Пациентка 30 лет, беременность 12 недель." in call.prompt
    assert "[F1]" in call.prompt
    assert "ОХЛП" in call.prompt
    assert "УУР B, УДД 2" in call.prompt
    assert RECOMMENDATION.text in call.prompt
    assert "дословн" in call.instructions
    # Вопрос и сведения о пациенте — данные: указания в них модель не выполняет.
    assert "не выполняй" in call.instructions
    assert "чисел, которых нет в цитате" in call.instructions


async def test_context_budget_keeps_fragments_up_to_24000_characters(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    script = Script({"claims": []})
    pieces = [Piece(f"Беременность, раздел {n}. " + "текст " * 1_660) for n in range(4)]
    assert all(9_900 < len(p.text) < 10_100 for p in pieces)

    await _answer(sessionmaker, script, pieces)

    [call] = script.calls
    assert "[F2]" in call.prompt
    assert "[F3]" not in call.prompt


async def test_single_fragment_over_budget_is_still_shown(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    script = Script({"claims": []})

    await _answer(sessionmaker, script, [Piece("Беременность. " + "текст " * 5_000)])

    [call] = script.calls
    assert "[F1]" in call.prompt


async def test_source_filter_reaches_search(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    script = Script({"claims": []})

    async with sessionmaker() as session:
        await add_document(session, "aviandr-ohlp", [CONTRAINDICATION], kind="drug_label")
        await session.commit()
    result = await service.answer(sessionmaker, _ai(script), QUESTION, source_ids=["kr-other"])

    assert result.run_id is None
    assert script.calls == []


async def test_model_is_called_after_search_session_is_closed(settings: Settings) -> None:
    # Своя фабрика сессий: в пуле её движка видно, держит ли поиск соединение.
    engine = make_engine(settings.database_url)
    maker = make_sessionmaker(engine)
    held: list[int] = []
    model = probe_model({"claims": []}, lambda: held.append(engine.pool.checkedout()))
    embedder, _ = words_embedder()
    try:
        async with maker() as session:
            await add_document(session, "aviandr-ohlp", [CONTRAINDICATION], kind="drug_label")
            await session.commit()

        result = await service.answer(
            maker, service.KnowledgeAi(embedder, Llm(model, MemoryRecorder())), QUESTION
        )
    finally:
        await engine.dispose()

    assert result.run_id is not None
    assert held == [0]


DOSING = Piece(
    "Приём препарата\nвозможен во время еды. Внутрь по 0,5 мг 2 раза в сутки.",
    section="4.2",
    section_title="Режим дозирования",
    heading_path="4.2. Режим дозирования",
)


async def test_quote_shorter_than_three_words_is_dropped(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    script = Script(
        {
            "claims": [
                _claim("F1", "беременность", "Нельзя при беременности."),
                _claim("F1", "период грудного вскармливания", "Нельзя при кормлении грудью."),
            ]
        }
    )

    result = await _answer(sessionmaker, script, [CONTRAINDICATION])

    assert [c.quote for c in result.claims] == ["период грудного вскармливания"]
    assert result.dropped == 1


async def test_number_missing_from_quote_is_dropped(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    script = Script(
        {
            "claims": [
                # 0.5 и 0,5 — одно число; «дважды» числом не считается.
                _claim("F1", "по 0,5 мг 2 раза в сутки", "Принимать по 0.5 мг дважды в сутки."),
                _claim("F1", "по 0,5 мг 2 раза в сутки", "Принимать по 0,5 мг 3 раза в сутки."),
                _claim("F1", "по 0,5 мг 2 раза", "Суточная доза — 1 мг."),
            ]
        }
    )

    result = await _answer(sessionmaker, script, [DOSING])

    assert [c.statement for c in result.claims] == ["Принимать по 0.5 мг дважды в сутки."]
    assert result.dropped == 2


async def test_doctor_sees_quote_as_written_in_fragment(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    script = Script({"claims": [_claim("F1", "  прием ПРЕПАРАТА   возможен ", "Можно с едой.")]})

    result = await _answer(sessionmaker, script, [DOSING])

    [claim] = result.claims
    assert claim.quote == "Приём препарата\nвозможен"
