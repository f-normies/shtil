"""Извлечение без базы: проверка цитат и кодов, сверка с ГРЛС, версия промпта (спека #30, 5)."""

import re
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from clin_compass_knowledge.grls import Registry
from pydantic import Field, create_model

from clin_compass.core.ai.llm import Llm
from clin_compass.modules.ingestion import analysis
from clin_compass.modules.ingestion.analysis import Extractor
from tests.core.fake_nodes import MemoryRecorder
from tests.freetext.helpers import Script

TEXT = (
    "Дата: 14.10.2026.\n"
    "Диагноз: Рекуррентное депрессивное расстройство, текущий эпизод средней степени (F33.1).\n"
    "Принимал Sertralini 50 мг утром, отменён 04.10.2026. Лечился пароксетином 20 мг/сут.\n"
    "Сейчас суматриптан 50 мг при мигрени. Отец злоупотреблял алкоголем."
)


def answer(**fields: Any) -> dict[str, Any]:
    """Ответ модели: пустые списки, кроме переданных."""
    return {
        "document_date": None,
        "diagnoses": [],
        "medications": [],
        "anamnesis": [],
        "risk_factors": [],
        **fields,
    }


def diagnosis(
    icd10: str = "F33.1", *, code_in_text: bool = True, quote: str = ""
) -> dict[str, Any]:
    return {
        "icd10": icd10,
        "code_in_text": code_in_text,
        "text": "Рекуррентное депрессивное расстройство, текущий эпизод средней степени",
        "quote": quote or "Рекуррентное депрессивное расстройство, текущий эпизод средней степени",
    }


def medication(name: str, name_in_text: str, quote: str, **fields: Any) -> dict[str, Any]:
    return {
        "name": name,
        "name_in_text": name_in_text,
        "dose": "50 мг",
        "regimen": "утром",
        "status": "current",
        "stopped_on": None,
        "stopped_days_before_document": None,
        "quote": quote,
        **fields,
    }


def fact(text: str) -> dict[str, Any]:
    return {"fact": text, "quote": text}


async def extract(registry: Registry, reply: dict[str, Any], *, ocr: bool = False) -> Any:
    extractor = Extractor(Llm(Script(reply).model, MemoryRecorder()), registry)
    return await extractor.extract(TEXT, ocr=ocr)


async def test_made_up_quote_is_dropped(registry: Registry) -> None:
    reply = answer(
        anamnesis=[fact("Лечился пароксетином 20 мг/сут"), fact("Лечился флуоксетином")],
        risk_factors=[fact("Отец злоупотреблял алкоголем")],
    )

    result = await extract(registry, reply)

    assert [f.quote for f in result.facts] == [
        "Лечился пароксетином 20 мг/сут",
        "Отец злоупотреблял алкоголем",
    ]
    assert [f.field for f in result.facts] == ["anamnesis", "risk_factor"]
    assert result.dropped == 1


@pytest.mark.parametrize("code", ["депрессия", "F3", "F33.123", "33.1"])
async def test_code_out_of_format_is_dropped(registry: Registry, code: str) -> None:
    result = await extract(registry, answer(diagnoses=[diagnosis(code)]))

    assert (result.diagnoses, result.dropped) == ((), 1)


async def test_code_with_cyrillic_letter_is_read_as_latin(registry: Registry) -> None:
    # Модель или распознавание могли написать букву кода кириллицей: «Е11.9» — это E11.9.
    result = await extract(registry, answer(diagnoses=[diagnosis("Е11.9", code_in_text=False)]))

    assert [d.icd10 for d in result.diagnoses] == ["E11.9"]


async def test_code_in_text_is_kept_when_text_has_the_code(registry: Registry) -> None:
    result = await extract(registry, answer(diagnoses=[diagnosis("f33.1")]))

    [found] = result.diagnoses
    assert (found.icd10, found.code_in_text) == ("F33.1", True)


async def test_code_in_text_is_reset_when_text_has_no_code(registry: Registry) -> None:
    # В тексте F33.1, модель уточнила до F33.11 и уверяет, что код написан в тексте.
    result = await extract(registry, answer(diagnoses=[diagnosis("F33.11", code_in_text=True)]))

    [found] = result.diagnoses
    assert found.code_in_text is False


async def test_medication_gets_registry_check(registry: Registry) -> None:
    reply = answer(medications=[medication("суматриптан", "суматриптан", "суматриптан 50 мг")])

    [found] = (await extract(registry, reply)).medications

    assert found.drug.check == "registered"
    assert [m.trade_name for m in found.drug.matches] == ["Суматриптан"]


async def test_medication_not_in_registry_is_kept(registry: Registry) -> None:
    reply = answer(
        medications=[medication("пароксетин", "пароксетином", "Лечился пароксетином 20 мг/сут")]
    )

    [found] = (await extract(registry, reply)).medications

    assert (found.name, found.drug.check, found.drug.matches) == ("пароксетин", "not_found", ())


async def test_dose_and_form_are_not_part_of_checked_name(registry: Registry) -> None:
    reply = answer(medications=[medication("суматриптан 50 мг", "суматриптан", "суматриптан 50")])

    [found] = (await extract(registry, reply)).medications

    assert (found.drug.name, found.drug.check) == ("суматриптан", "registered")


@pytest.mark.parametrize(
    ("name", "name_in_text", "quote", "renamed"),
    [
        ("сертралин", "Sertralini", "Принимал Sertralini 50 мг утром", True),
        ("пароксетин", "пароксетином", "Лечился пароксетином 20 мг/сут", False),
        ("суматриптан", "Суматриптан", "Сейчас суматриптан 50 мг", False),
    ],
)
async def test_renamed_marks_a_name_the_model_changed(
    registry: Registry, name: str, name_in_text: str, quote: str, renamed: bool
) -> None:
    reply = answer(medications=[medication(name, name_in_text, quote)])

    [found] = (await extract(registry, reply)).medications

    assert found.renamed is renamed


async def test_made_up_name_not_in_registry_falls_back_to_the_text(registry: Registry) -> None:
    # Qwen3.6 не знает «Авиандр» и выдумывает МНН «авинантан»: в карточку идёт название из текста.
    text = "Назначен Авиандр по 20 мг 2 раза в сутки за 30 минут до еды."
    reply = answer(medications=[medication("авинантан", "Авиандр", "Назначен Авиандр по 20 мг")])
    extractor = Extractor(Llm(Script(reply).model, MemoryRecorder()), registry)

    [found] = (await extractor.extract(text, ocr=False)).medications

    assert (found.name, found.renamed, found.drug.name) == ("Авиандр", False, "Авиандр")


async def test_past_medication_keeps_stop_date(registry: Registry) -> None:
    reply = answer(
        document_date="2026-10-14",
        medications=[
            medication(
                "сертралин",
                "Sertralini",
                "Принимал Sertralini 50 мг утром, отменён 04.10.2026",
                status="past",
                stopped_on="2026-10-04",
            )
        ],
    )

    result = await extract(registry, reply)

    assert result.document_date == date(2026, 10, 14)
    [found] = result.medications
    assert (found.status, found.stopped_on) == ("past", date(2026, 10, 4))


@pytest.mark.parametrize("value", ["14.10.2026", "2026-02-30", "октябрь"])
async def test_date_out_of_format_becomes_none(registry: Registry, value: str) -> None:
    result = await extract(registry, answer(document_date=value))

    assert result.document_date is None


async def test_current_medication_has_no_stop_date(registry: Registry) -> None:
    reply = answer(
        medications=[
            medication("суматриптан", "суматриптан", "суматриптан 50 мг", stopped_on="2026-10-04")
        ]
    )

    [found] = (await extract(registry, reply)).medications

    assert found.stopped_on is None


@pytest.mark.parametrize(
    ("ocr", "kind"),
    [
        (False, "Вид документа: PDF с текстовым слоем."),
        (True, "Вид документа: текст распознан с изображения, названия и цифры могли исказиться."),
    ],
)
async def test_document_kind_is_in_the_input(registry: Registry, ocr: bool, kind: str) -> None:
    script = Script(answer())
    extractor = Extractor(Llm(script.model, MemoryRecorder()), registry)

    await extractor.extract(TEXT, ocr=ocr)

    [call] = script.calls
    assert call.prompt.startswith(kind)
    assert f"<текст>\n{TEXT}\n</текст>" in call.prompt
    assert call.schema["required"] == [
        "document_date",
        "diagnoses",
        "medications",
        "anamnesis",
        "risk_factors",
    ]


async def test_run_is_recorded_with_task_and_prompt_version(registry: Registry) -> None:
    recorder = MemoryRecorder()
    extractor = Extractor(Llm(Script(answer()).model, recorder), registry)

    await extractor.extract(TEXT, ocr=False)

    [run] = recorder.runs
    assert (run.task, run.prompt_version) == ("ingestion.extract", extractor.prompt_version)
    assert run.params["max_tokens"] == 6144
    assert run.params["temperature"] == 0


def test_prompt_version_follows_instructions_wrapper_and_schema(
    registry: Registry, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    llm = Llm(Script(answer()).model, MemoryRecorder())
    base = Extractor(llm, registry).prompt_version
    assert re.fullmatch(r"extract-[0-9a-f]{8}", base)
    assert Extractor(llm, registry).prompt_version == base

    prompts = tmp_path / "prompts"
    prompts.mkdir()
    edited = (analysis.PROMPTS / "extract.md").read_text(encoding="utf-8") + "\nЕщё правило.\n"
    (prompts / "extract.md").write_text(edited, encoding="utf-8")
    with monkeypatch.context() as patch:
        patch.setattr(analysis, "PROMPTS", prompts)
        assert Extractor(llm, registry).prompt_version != base

    with monkeypatch.context() as patch:
        patch.setattr(analysis, "_INPUT", "Документ: {kind}.\n{text}")
        assert Extractor(llm, registry).prompt_version != base

    wider = create_model(
        "Extraction",
        __base__=analysis.ExtractionOutput,
        note=(str, Field(description="ещё поле")),
    )
    with monkeypatch.context() as patch:
        patch.setattr(analysis, "ExtractionOutput", wider)
        assert Extractor(llm, registry).prompt_version != base
