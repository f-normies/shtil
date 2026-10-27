"""Метрики оценки разбора текста и согласованность эталонного набора (спека #32, раздел 7).

uv run --package clin-compass-eval pytest eval/freetext
"""

from pathlib import Path

import pytest
from clin_compass.modules.freetext.analysis import FoundSign, TextFinding, quote_in_text
from clin_compass_knowledge.grls import DrugCheck, DrugMatch, load_registry
from clin_compass_knowledge.loader import load_knowledge

from metrics import CASES, Case, Prediction, aggregate, load_cases, quotes_overlap, score_case

KNOWLEDGE = Path(__file__).resolve().parents[2] / "knowledge"


def _finding(
    category: str, quote: str, drug: DrugCheck | None = None, event: str | None = None
) -> TextFinding:
    return TextFinding(category, quote, quote, drug, event)  # type: ignore[arg-type]


CASE = Case.model_validate(
    {
        "id": "t",
        "source_type": "diary",
        "text": "Принимаю авиандор утром. Голова болит по вечерам. Не хочу жить.",
        "findings": [
            {
                "category": "adherence",
                "quote": "Принимаю авиандор утром",
                "adherence_event": "taken",
            },
            {"category": "side_effect", "quote": "Голова болит по вечерам"},
        ],
        "drugs": [{"name": "авиандор", "check": "similar"}],
        "signs": ["suicidal-thoughts"],
    }
)


@pytest.mark.parametrize(
    ("a", "b", "overlap"),
    [
        ("Голова болит по вечерам", "голова болит по вечерам", True),
        ("болит по вечерам", "Голова болит по вечерам", True),
        ("Голова болит", "по вечерам тошнит", False),
        ("", "Голова болит", False),
    ],
)
def test_quotes_overlap(a: str, b: str, overlap: bool) -> None:
    assert quotes_overlap(a, b) is overlap


def test_score_counts_matches_misses_and_extras() -> None:
    aviandr = DrugCheck("авиандор", "similar", (DrugMatch("АВИАНДР®", "Маритупирдин", "ЛП-1"),))
    prediction = Prediction(
        findings=(
            _finding("adherence", "авиандор утром", aviandr, "missed"),
            _finding("symptom", "Голова болит по вечерам"),  # не та категория
        ),
        risk_signs=(FoundSign("suicidal-thoughts", "Не хочу жить", quote_found=True),),
        extract_signs=(),
        dropped=1,
    )

    score = score_case(CASE, prediction)

    assert score.findings["adherence"] == (1, 0, 0)  # (совпало, лишние, пропущено)
    assert score.findings["side_effect"] == (0, 0, 1)
    assert score.findings["symptom"] == (0, 1, 0)
    assert (score.adherence_correct, score.adherence_matched) == (0, 1)
    assert (score.drugs_correct, score.drugs_expected, score.drugs_missed) == (1, 1, ())
    assert (score.risk_expected, score.risk_check, score.risk_extract) == (True, True, False)
    assert score.risk_check_kinds == ({"suicidal-thoughts"}, {"suicidal-thoughts"})
    assert score.dropped == 1


def test_aggregate_risk_recall_and_misses() -> None:
    caught = Prediction((), (FoundSign("suicidal-thoughts", "x", True),), (), 0)
    missed = Prediction((), (), (), 0)
    quiet = CASE.model_copy(update={"id": "q", "signs": [], "findings": [], "drugs": []})

    report = aggregate(
        [
            score_case(CASE, caught),
            score_case(CASE.model_copy(update={"id": "m"}), missed),
            score_case(quiet, missed),
        ]
    )

    assert report["risk"]["check_risk"]["recall"] == 0.5
    assert report["risk"]["check_risk"]["precision"] == 1.0
    assert report["risk"]["missed_by_both"] == ["m"]
    # Модель не назвала препарат — промах виден по тексту и названию.
    assert report["drugs_missed"] == ["t: авиандор", "m: авиандор"]


def test_cases_are_consistent_with_text_signs_and_registry() -> None:
    # Ошибка в эталоне исказила бы цифры для презентации: цитаты из текста, виды признаков —
    # из knowledge/, ожидаемый итог сверки — тот, что даёт снимок ГРЛС для этого названия.
    cases = load_cases(CASES)
    signs = {s.id for s in load_knowledge(KNOWLEDGE).signs.values() if s.status == "reviewed"}
    registry = load_registry(KNOWLEDGE / "sources" / "files" / "grls.csv")

    for case in cases:
        for finding in case.findings:
            assert quote_in_text(finding.quote, case.text), (case.id, finding.quote)
        assert set(case.signs) <= signs, case.id
        for drug in case.drugs:
            assert registry.lookup(drug.name).check == drug.check, (case.id, drug.name)

    assert len(cases) >= 40
    assert sum(1 for c in cases if c.signs) >= 12
