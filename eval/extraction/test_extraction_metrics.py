"""Совпадения и метрики извлечения из историй болезни (спека #30, раздел 7).

uv run --package clin-compass-eval pytest eval/extraction
"""

from datetime import date
from typing import Any
from uuid import uuid4

import pytest
from clin_compass.modules.ingestion.analysis import (
    Extraction,
    FoundDiagnosis,
    FoundFact,
    FoundMedication,
)
from clin_compass_knowledge.grls import DrugCheck
from clin_compass_knowledge.schemas.case import Expected

from extraction_metrics import (
    aggregate,
    dose_equal,
    load_cases,
    quotes_overlap,
    regimen_equal,
    score_case,
)


def found_diagnosis(code: str, *, in_text: bool = True) -> FoundDiagnosis:
    return FoundDiagnosis(code, "диагноз", "цитата", in_text)


def found_drug(
    name: str, dose: str | None = None, regimen: str | None = None, **kw: Any
) -> FoundMedication:
    return FoundMedication(
        name=name,
        name_in_text=name,
        renamed=False,
        dose=dose,
        regimen=regimen,
        status=kw.get("status", "current"),
        stopped_on=None,
        stopped_on_source=None,
        quote="цитата",
        drug=DrugCheck(name, "not_found", ()),
    )


def extraction(
    diagnoses: tuple[FoundDiagnosis, ...] = (),
    medications: tuple[FoundMedication, ...] = (),
    facts: tuple[FoundFact, ...] = (),
    dropped: int = 0,
) -> Extraction:
    return Extraction(date(2026, 10, 14), diagnoses, medications, facts, dropped, uuid4())


def expected(**fields: Any) -> Expected:
    return Expected.model_validate(
        {"diagnoses": [], "medications": [], "risk_factors": [], "anamnesis": [], **fields}
    )


def drug(
    name: str, dose: str | None = None, regimen: str | None = None, status: str = "current"
) -> dict[str, Any]:
    return {"name": name, "dose": dose, "regimen": regimen, "status": status}


@pytest.mark.parametrize(
    ("a", "b", "same"),
    [
        ("0,5 мг", "0.5 мг", True),
        ("100 мг/сут", "100мг/сут", True),
        ("75 мг", "150 мг", False),
        (None, None, True),
        ("50 мг", None, False),
    ],
)
def test_dose_ignores_spaces_and_decimal_comma(a: str | None, b: str | None, same: bool) -> None:
    assert dose_equal(a, b) is same


@pytest.mark.parametrize(
    ("a", "b", "same"),
    [
        ("3 раза в день", "3 раза в день во время еды", True),
        ("2 раза в день во время еды", "2 раза в день", True),
        ("на ночь", "утром", False),
        (None, None, True),
        (None, "утром", False),
    ],
)
def test_regimen_needs_half_of_words_in_common(a: str | None, b: str | None, same: bool) -> None:
    assert regimen_equal(a, b) is same


@pytest.mark.parametrize(
    ("a", "b", "overlap"),
    [
        ("Мать страдала депрессией", "мать страдала депрессией", True),
        ("страдала депрессией", "Мать страдала депрессией", True),
        ("Алкоголь не употребляет", "Мать страдала депрессией", False),
        ("", "Мать", False),
    ],
)
def test_quotes_overlap(a: str, b: str, overlap: bool) -> None:
    assert quotes_overlap(a, b) is overlap


def test_diagnoses_match_by_code_once_each() -> None:
    case = expected(diagnoses=[{"icd10": "F33.2", "text": "РДР"}, {"icd10": "Z39.1", "text": "Л"}])
    found = extraction(
        diagnoses=(found_diagnosis("F33.2"), found_diagnosis("F33.2"), found_diagnosis("F32.1"))
    )

    score = score_case("c", case, found)

    assert score.fields["diagnoses"] == (1, 2, 1)


def test_medications_match_by_name_and_check_dose_regimen_status() -> None:
    case = expected(
        medications=[
            drug("венлафаксин", "75 мг", "3 раза в день"),
            drug("кветиапин", "50 мг", "на ночь"),
            drug("сертралин", "100 мг/сут", None, "past"),
        ]
    )
    found = extraction(
        medications=(
            found_drug("Венлафаксин", "75 мг", "3 раза в день"),
            found_drug("кветиапин", "25 мг", "на ночь"),
            found_drug("сертралин", "100 мг/сут", None, status="current"),
            found_drug("мелатонин"),
        )
    )

    score = score_case("c", case, found)

    assert score.fields["medications"] == (3, 1, 0)
    assert (score.dose, score.regimen, score.status) == ((2, 3), (3, 3), (2, 3))


def test_misspelled_name_still_matches() -> None:
    case = expected(medications=[drug("флувоксамин")])

    score = score_case("c", case, extraction(medications=(found_drug("флуоксамин"),)))

    assert score.fields["medications"] == (1, 0, 0)


def test_facts_match_by_quote_overlap_in_their_field() -> None:
    case = expected(
        risk_factors=[{"fact": "депрессия у матери", "quote": "Мать страдала депрессией"}],
        anamnesis=[{"fact": "не пьёт", "quote": "Алкоголь не употребляет"}],
    )
    found = extraction(
        facts=(
            FoundFact("anamnesis", "мать", "Мать страдала депрессией"),
            FoundFact("anamnesis", "не пьёт", "Алкоголь не употребляет"),
        )
    )

    score = score_case("c", case, found)

    assert score.fields["anamnesis"] == (1, 1, 0)
    assert score.fields["risk_factors"] == (0, 0, 1)


def test_codes_proposed_by_model_are_checked_against_expected() -> None:
    case = expected(diagnoses=[{"icd10": "F32.0", "text": "ЛДЭ"}])
    found = extraction(
        diagnoses=(
            found_diagnosis("F32.0", in_text=False),
            found_diagnosis("F41.1", in_text=False),
            found_diagnosis("E11.9", in_text=True),
        )
    )

    assert score_case("c", case, found).codes_proposed == (1, 2)


def test_aggregate_gives_precision_recall_f1_per_field() -> None:
    case = expected(
        diagnoses=[{"icd10": "F33.2", "text": "РДР"}, {"icd10": "F41.1", "text": "ГТР"}]
    )
    found = extraction(diagnoses=(found_diagnosis("F33.2"), found_diagnosis("F32.1")), dropped=2)

    result = aggregate([score_case("c", case, found)])

    assert result["fields"]["diagnoses"] == {
        "precision": 0.5,
        "recall": 0.5,
        "f1": 0.5,
        "tp": 1,
        "fp": 1,
        "fn": 1,
    }
    assert result["fields"]["medications"]["precision"] is None
    assert result["dropped_share"] == 0.5


def test_all_twenty_histories_load() -> None:
    cases = load_cases()

    assert [c.id for c in cases] == [f"case-{i:02d}" for i in range(1, 21)]
    assert all(c.history and c.expected.diagnoses for c in cases)


def test_best_pairs_win_over_first_found() -> None:
    # Первый ответ подходит обоим эталонным фактам, второй — только первому.
    # Первое попавшееся совпадение оставило бы второй эталонный факт без пары.
    case = expected(
        anamnesis=[
            {"fact": "не пьёт", "quote": "Алкоголь не употребляет"},
            {"fact": "редко", "quote": "редко, по праздникам"},
        ]
    )
    found = extraction(
        facts=(
            FoundFact("anamnesis", "редко", "Алкоголь употребляет редко, по праздникам"),
            FoundFact("anamnesis", "не пьёт", "Алкоголь не употребляет вовсе"),
        )
    )

    assert score_case("c", case, found).fields["anamnesis"] == (2, 0, 0)


def test_closer_drug_name_wins() -> None:
    # «флувоксамин» модели ближе ко второму эталонному названию, чем к первому, похожему.
    case = expected(medications=[drug("флуоксамин"), drug("флувоксамин")])

    score = score_case("c", case, extraction(medications=(found_drug("флувоксамин"),)))

    assert (score.fields["medications"], score.missed["medications"]) == ((1, 0, 1), ["флуоксамин"])
