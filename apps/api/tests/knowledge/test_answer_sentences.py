"""Ответ с цитатой по номерам частей и лимиты ответа (спека #70, 6.1–6.2).

LLM — `FunctionModel` с заданным ответом. Режим дословной цитаты проверяет `test_answer.py`.
"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.core.ai.llm import Llm
from clin_compass.modules.knowledge import service
from clin_compass.modules.knowledge.schemas import Answer
from tests.core.fake_nodes import MemoryRecorder
from tests.freetext.helpers import Script
from tests.knowledge.helpers import Piece, add_document, words_embedder

QUESTION = "Как принимать маритупирдин и сколько длится курс?"
DOSING = Piece(
    "Принимать по 20 мг один раз в сутки. Приём препарата возможен независимо от еды. "
    "Курс — 4 недели.",
    section="4.2",
    section_title="Режим дозирования",
    heading_path="4.2. Режим дозирования",
)


def _ai(script: Script, **options: Any) -> service.KnowledgeAi:
    embedder, _ = words_embedder()
    return service.KnowledgeAi(embedder, Llm(script.model, MemoryRecorder()), **options)


async def _answer(
    sessionmaker: async_sessionmaker[AsyncSession],
    script: Script,
    pieces: list[Piece],
    **options: Any,
) -> Answer:
    async with sessionmaker() as session:
        await add_document(session, "aviandr-ohlp", pieces, kind="drug_label", title="ОХЛП")
        await session.commit()
    return await service.answer(sessionmaker, _ai(script, **options), QUESTION)


def _claim(first: int, last: int, statement: str = "Принимают по 20 мг.") -> dict[str, Any]:
    return {"statement": statement, "fragment": "F1", "first": first, "last": last}


def _item_schema(schema: dict[str, Any]) -> dict[str, Any]:
    item = schema["properties"]["claims"]["items"]
    return schema["$defs"][item["$ref"].split("/")[-1]] if "$ref" in item else item


async def test_prompt_numbers_fragment_parts(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    script = Script({"claims": []})

    await _answer(sessionmaker, script, [DOSING], citation="sentences")

    [call] = script.calls
    assert (
        "(1) Принимать по 20 мг один раз в сутки.\n"
        "(2) Приём препарата возможен независимо от еды.\n"
        "(3) Курс — 4 недели."
    ) in call.prompt


async def test_quote_is_cut_from_unit_range(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    script = Script({"claims": [_claim(1, 2, "Принимают по 20 мг раз в сутки с едой или без.")]})

    result = await _answer(sessionmaker, script, [DOSING], citation="sentences")

    [claim] = result.claims
    assert (
        claim.quote
        == "Принимать по 20 мг один раз в сутки. Приём препарата возможен независимо от еды."
    )
    assert result.dropped == 0


async def test_range_outside_fragment_is_dropped(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    script = Script({"claims": [_claim(3, 5, "Курс — 4 недели.")]})

    result = await _answer(sessionmaker, script, [DOSING], citation="sentences")

    assert (result.claims, result.dropped) == ([], 1)


async def test_range_longer_than_four_parts_is_dropped(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    long = Piece(
        " ".join(f"Предложение номер {n} о препарате." for n in range(1, 7)), section="4.2"
    )
    script = Script({"claims": [_claim(1, 5, "Длинная цитата.")]})

    result = await _answer(sessionmaker, script, [long], citation="sentences")

    assert result.dropped == 1


async def test_number_missing_from_parts_is_dropped(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    script = Script({"claims": [_claim(1, 1, "Принимают по 40 мг.")]})

    result = await _answer(sessionmaker, script, [DOSING], citation="sentences")

    assert result.dropped == 1


async def test_schema_has_unit_range_and_claim_limit(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    script = Script({"claims": []})

    await _answer(sessionmaker, script, [DOSING], citation="sentences", max_claims=5)

    [call] = script.calls
    assert call.schema["properties"]["claims"]["maxItems"] == 5
    properties = _item_schema(call.schema)["properties"]
    assert {"statement", "fragment", "first", "last"} == set(properties)
    assert properties["first"]["minimum"] == 1


async def test_max_fragments_limits_search_and_labels(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    pieces = [
        Piece(f"Маритупирдин курс {n}: принимать по 20 мг.", section=f"4.{n}") for n in range(1, 8)
    ]
    script = Script({"claims": []})

    await _answer(sessionmaker, script, pieces, max_fragments=5)

    [call] = script.calls
    assert "[F5]" in call.prompt and "[F6]" not in call.prompt
    assert _item_schema(call.schema)["properties"]["fragment"]["enum"] == [
        f"F{n}" for n in range(1, 6)
    ]


async def test_defaults_are_sentences_with_five_fragments_and_claims(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    # Вариант, выбранный подбором #70 (спека, 6.5; ADR-0009).
    pieces = [
        Piece(f"Маритупирдин курс {n}: принимать по 20 мг.", section=f"4.{n}") for n in range(1, 8)
    ]
    script = Script({"claims": []})

    await _answer(sessionmaker, script, pieces)

    [call] = script.calls
    assert "(1) Маритупирдин курс" in call.prompt
    assert "[F5]" in call.prompt and "[F6]" not in call.prompt
    assert call.schema["properties"]["claims"]["maxItems"] == 5
    assert {"first", "last"} <= set(_item_schema(call.schema)["properties"])


def test_prompt_version_depends_on_mode_and_limits() -> None:
    script = Script({"claims": []})
    versions = {
        _ai(script).prompt_version,
        _ai(script, citation="quote").prompt_version,
        _ai(script, max_fragments=8, max_claims=8).prompt_version,
    }

    assert len(versions) == 3
