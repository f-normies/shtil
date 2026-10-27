"""Разбор текста без базы: агенты, проверка цитат, сверка с ГРЛС (спека #32, разделы 4.2–4.3).

Модель — `FunctionModel` с заранее заданным ответом: LLM в тестах не вызывается.
"""

import re

import pytest
from clin_compass_knowledge.grls import DrugMatch, Registry
from clin_compass_knowledge.loader import Knowledge

from clin_compass.core.ai.llm import Llm
from clin_compass.core.ai.nodes import AiOutputError
from clin_compass.modules.freetext import analysis
from clin_compass.modules.freetext.analysis import Analyzer, FoundSign, quote_in_text
from tests.core.fake_nodes import MemoryRecorder
from tests.freetext.helpers import Script

TEXT = "Принимаю авиандор утром. По вечерам голова иногда болит. Иногда думаю, что не хочу жить."
AVIANDR = DrugMatch("АВИАНДР®", "Маритупирдин", "ЛП-№(002445)-(РГ-RU)")


def _analyzer(script: Script, knowledge: Knowledge, registry: Registry) -> Analyzer:
    return Analyzer(Llm(script.model, MemoryRecorder()), knowledge.signs.values(), registry)


def _finding(category: str, quote: str, **extra: object) -> dict[str, object]:
    return {
        "category": category,
        "summary": quote,
        "quote": quote,
        "drug_name": None,
        "adherence_event": None,
        **extra,
    }


async def test_finding_with_quote_missing_from_text_is_dropped(
    knowledge: Knowledge, registry: Registry
) -> None:
    script = Script(
        {
            "findings": [
                _finding("side_effect", "По вечерам голова иногда болит"),
                _finding("symptom", "панические атаки каждую ночь"),
            ],
            "signs": [],
        }
    )

    result = await _analyzer(script, knowledge, registry).extract(
        TEXT, source_type="diary", transcript=False
    )

    assert [f.quote for f in result.findings] == ["По вечерам голова иногда болит"]
    assert result.dropped == 1


async def test_risk_sign_with_quote_missing_from_text_is_kept_and_marked(
    knowledge: Knowledge, registry: Registry
) -> None:
    script = Script({"signs": [{"kind": "test-thoughts", "quote": "хочу умереть"}]})

    result = await _analyzer(script, knowledge, registry).check_risk(
        TEXT, source_type="message", transcript=False
    )

    assert result.signs == (FoundSign("test-thoughts", "хочу умереть", quote_found=False),)


async def test_drug_name_is_checked_against_registry(
    knowledge: Knowledge, registry: Registry
) -> None:
    script = Script(
        {
            "findings": [
                _finding(
                    "adherence",
                    "Принимаю авиандор утром",
                    drug_name="авиандор",
                    adherence_event="taken",
                ),
                _finding("side_effect", "По вечерам голова иногда болит"),
            ],
            "signs": [],
        }
    )

    result = await _analyzer(script, knowledge, registry).extract(
        TEXT, source_type="diary", transcript=True
    )

    with_drug, without_drug = result.findings
    assert with_drug.drug is not None
    assert (with_drug.drug.check, with_drug.drug.matches) == ("similar", (AVIANDR,))
    assert with_drug.adherence_event == "taken"
    assert without_drug.drug is None


@pytest.mark.parametrize(
    ("quote", "found"),
    [
        ("По вечерам голова иногда болит", True),
        ("по вечерам   ГОЛОВА иногда болит", True),
        ("«голова иногда болит».", True),
        ("голова болит", False),
        ("", False),
    ],
)
def test_quote_in_text(quote: str, found: bool) -> None:
    assert quote_in_text(quote, TEXT) is found


def test_quote_ignores_yo() -> None:
    assert quote_in_text("всё ещё плохо", "Всё еще плохо сплю")


async def test_prompt_lists_only_reviewed_signs(knowledge: Knowledge, registry: Registry) -> None:
    script = Script({"signs": []})

    await _analyzer(script, knowledge, registry).check_risk(
        TEXT, source_type="diary", transcript=False
    )

    [call] = script.calls
    assert "test-intent" in call.instructions
    assert "Пациент пишет о плане или приготовлениях." in call.instructions
    assert "test-draft" not in call.instructions


async def test_sign_kind_is_limited_to_reviewed_signs(
    knowledge: Knowledge, registry: Registry
) -> None:
    script = Script({"signs": [{"kind": "test-draft", "quote": "не хочу жить"}]})

    with pytest.raises(AiOutputError):
        await _analyzer(script, knowledge, registry).check_risk(
            TEXT, source_type="diary", transcript=False
        )

    sign_schema = script.calls[0].schema["$defs"]
    kinds = next(
        d["properties"]["kind"]["enum"] for d in sign_schema.values() if "kind" in d["properties"]
    )
    assert kinds == ["test-intent", "test-thoughts"]


async def test_transcript_prompt_warns_about_recognition_errors(
    knowledge: Knowledge, registry: Registry
) -> None:
    script = Script({"findings": [], "signs": []})
    analyzer = _analyzer(script, knowledge, registry)

    await analyzer.extract(TEXT, source_type="message", transcript=True)
    await analyzer.extract(TEXT, source_type="message", transcript=False)

    transcript, written = script.calls
    assert "расшифровка голосового" in transcript.prompt
    assert "расшифровка голосового" not in written.prompt
    assert TEXT in written.prompt


def test_prompt_version_follows_sign_definitions(knowledge: Knowledge, registry: Registry) -> None:
    llm = Llm(Script({}).model, MemoryRecorder())
    signs = list(knowledge.signs.values())
    edited = [
        s.model_copy(update={"definition": s.definition + " Ещё пример."})
        if s.id == "test-thoughts"
        else s
        for s in signs
    ]

    base = Analyzer(llm, signs, registry)
    again = Analyzer(llm, signs, registry)
    changed = Analyzer(llm, edited, registry)

    assert re.fullmatch(r"risk-[0-9a-f]{8}", base.risk_prompt_version)
    assert re.fullmatch(r"extract-[0-9a-f]{8}", base.extract_prompt_version)
    assert again.risk_prompt_version == base.risk_prompt_version
    assert changed.risk_prompt_version != base.risk_prompt_version
    assert changed.extract_prompt_version != base.extract_prompt_version


async def test_runs_are_recorded_with_task_and_prompt_version(
    knowledge: Knowledge, registry: Registry
) -> None:
    recorder = MemoryRecorder()
    analyzer = Analyzer(
        Llm(Script({"signs": []}).model, recorder), knowledge.signs.values(), registry
    )

    await analyzer.check_risk(TEXT, source_type="diary", transcript=False)

    [run] = recorder.runs
    assert (run.task, run.prompt_version) == ("freetext.check_risk", analyzer.risk_prompt_version)


def test_analyzer_needs_a_reviewed_sign(knowledge: Knowledge, registry: Registry) -> None:
    drafts = [s for s in knowledge.signs.values() if s.status == "draft"]

    with pytest.raises(ValueError, match="нет видов признаков риска со статусом reviewed"):
        Analyzer(Llm(Script({}).model, MemoryRecorder()), drafts, registry)


def test_quote_must_match_whole_words() -> None:
    # «плохо» внутри «неплохо» — выдуманная цитата, а не найденная.
    assert not quote_in_text("плохо", "Сплю неплохо, гуляю.")
    assert quote_in_text("сплю неплохо", "Сплю неплохо, гуляю.")


async def test_generic_word_instead_of_drug_name_gives_no_check(
    knowledge: Knowledge, registry: Registry
) -> None:
    # «Пропустила таблетку»: общее слово не сверяется, иначе врач увидел бы «нет в ГРЛС».
    script = Script(
        {
            "findings": [
                _finding("adherence", "Принимаю авиандор утром", drug_name="таблетки"),
            ],
            "signs": [],
        }
    )

    result = await _analyzer(script, knowledge, registry).extract(
        TEXT, source_type="diary", transcript=False
    )

    assert result.findings[0].drug is None


def test_prompt_version_covers_input_wrapper(
    knowledge: Knowledge, registry: Registry, monkeypatch: pytest.MonkeyPatch
) -> None:
    llm = Llm(Script({}).model, MemoryRecorder())
    before = Analyzer(llm, knowledge.signs.values(), registry)
    monkeypatch.setattr(analysis, "_TEXT_KIND", {"diary": "дневник", "message": "чат"})

    after = Analyzer(llm, knowledge.signs.values(), registry)

    assert after.risk_prompt_version != before.risk_prompt_version
    assert after.extract_prompt_version != before.extract_prompt_version


async def test_supplement_with_digit_in_name_is_flagged_not_found(
    knowledge: Knowledge, registry: Registry
) -> None:
    script = Script(
        {
            "findings": [_finding("medication", "Принимаю авиандор утром", drug_name="омега-3")],
            "signs": [],
        }
    )

    result = await _analyzer(script, knowledge, registry).extract(
        TEXT, source_type="diary", transcript=False
    )

    drug = result.findings[0].drug
    assert drug is not None
    assert (drug.name, drug.check) == ("омега-3", "not_found")
