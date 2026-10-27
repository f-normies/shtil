"""Записи прогонов v3, правило этапа A и слияние оценок разметки (спеки #92 и #105)."""

import json
from pathlib import Path

import pytest

from dataset import Question, Relevant
from test_v3_metrics import FP, _question
from v3_io import (
    merge_grades,
    merge_v2_grades,
    none_with_answer,
    outcome_from_record,
    passes_stage_a,
    unjudged,
)
from v3_report import load_run


def test_record_becomes_outcome() -> None:
    record = {
        "id": "s-001",
        "found": ["a/1", "x/1"],
        "shown": ["a/1"],
        "claims": [{"key": "a/1", "statement": "у", "quote": "ц"}],
        "dropped": 0,
        "latency_s": 9.5,
        "stages": {"answer": 8.0, "rewrite": 1.5},
        "verdicts": ["supported"],
        "points": [True, False],
        "must_not": [False],
    }

    outcome = outcome_from_record(record, _question())

    assert outcome.cited == ("a/1",)
    assert outcome.points == (True, False)
    assert outcome.claim_verdicts == ("supported",)
    assert outcome.stages == {"answer": 8.0, "rewrite": 1.5}


def test_unjudged_record_has_no_verdicts() -> None:
    record = {
        "id": "s-001",
        "found": [],
        "shown": [],
        "claims": [{"key": "a/1", "statement": "у", "quote": "ц"}],
        "dropped": 0,
        "latency_s": 1.0,
        "stages": {},
        "verdicts": None,
        "points": None,
        "must_not": None,
    }

    outcome = outcome_from_record(record, _question())

    assert outcome.claim_verdicts == (None,)
    assert outcome.points is None


@pytest.mark.parametrize(
    ("field", "verdicts"),
    [("points", [True]), ("points", [True, False, True]), ("must_not", [False, True])],
)
def test_verdicts_for_another_reference_are_rejected(field: str, verdicts: list[bool]) -> None:
    # Медик поправил эталон после судей: вердикты по прежним пунктам сдвинули бы метрики.
    record = {
        "id": "s-001",
        "found": [],
        "shown": [],
        "claims": [],
        "dropped": 0,
        "latency_s": 1.0,
        "verdicts": [],
        "points": [True, False],
        "must_not": [False],
    }
    record[field] = verdicts

    with pytest.raises(ValueError, match="эталон"):
        outcome_from_record(record, _question())


@pytest.mark.parametrize(
    ("v3_diffs", "v2_diff", "passed"),
    [
        (
            {"recall_at_5": (0.05, -0.01, 0.1), "context_coverage": (0.0, -0.03, 0.03)},
            (0.0, -0.01, 0.01),
            True,
        ),
        (
            {"recall_at_5": (0.0, -0.03, 0.03), "context_coverage": (0.04, 0.0, 0.08)},
            (0.0, -0.01, 0.01),
            True,
        ),
        (
            {"recall_at_5": (0.05, -0.03, 0.1), "context_coverage": (-0.01, -0.05, 0.03)},
            (0.0, -0.01, 0.01),
            False,
        ),
        (
            {"recall_at_5": (0.05, -0.01, 0.1), "context_coverage": (0.0, -0.01, 0.01)},
            (-0.02, -0.05, 0.0),
            False,
        ),
        (
            {"recall_at_5": (-0.01, -0.03, 0.01), "context_coverage": (-0.01, -0.02, 0.0)},
            (0.0, -0.01, 0.01),
            False,
        ),
    ],
)
def test_stage_a_rule(
    v3_diffs: dict[str, tuple[float, float, float]],
    v2_diff: tuple[float, float, float],
    passed: bool,
) -> None:
    assert passes_stage_a(v3_diffs, v2_diff) is passed


def test_scales_only_need_no_loss() -> None:
    flat = {"recall_at_5": (0.0, -0.01, 0.01), "context_coverage": (0.0, -0.01, 0.01)}

    assert passes_stage_a(flat, (0.0, -0.01, 0.01), require_gain=False) is True
    assert passes_stage_a(flat, (0.0, -0.01, 0.01)) is False


def test_judge_grades_merge_with_evidence() -> None:
    question = _question()

    prints = {key: FP for key in ("a/1", "a/3", "x/1", "x/2")}

    merged = merge_grades(question, {"a/1": 0, "a/3": 2, "x/1": 1, "x/2": 0}, prints)

    assert [(r.key, r.grade) for r in merged.relevant] == [
        ("a/1", 2),
        ("a/2", 2),
        ("a/3", 2),
        ("x/1", 1),
    ]


def test_judge_cannot_mark_answer_for_question_without_answer() -> None:
    question = _question(type="none", kind="none", points=[], must_not=[], relevant=[])

    merged = merge_grades(question, {"x/1": 2}, {"x/1": FP})

    assert [(r.key, r.grade) for r in merged.relevant] == [("x/1", 1)]


def test_fragments_with_answer_for_question_without_answer_are_listed() -> None:
    # Медик решает, верен ли вид `none`, по фрагментам, где судья нашёл ответ (PR #101, s-052).
    grades = {"x/2": 2, "x/1": 2, "x/3": 1, "x/4": 0}

    assert none_with_answer("none", grades) == ["x/1", "x/2"]


def test_fragments_with_answer_are_not_listed_for_question_with_answer() -> None:
    assert none_with_answer("single", {"x/1": 2}) == []


def _v2(kind: str, relevant: list[tuple[str, int]]) -> Question:
    return Question(
        id="ohlp-001",
        question="вопрос",
        kind=kind,  # type: ignore[arg-type]
        split="test",
        origin=["a/1"] if kind != "none" else [],
        relevant=[Relevant(key=k, fingerprint=FP, grade=g) for k, g in relevant],  # type: ignore[arg-type]
    )


def test_v2_grades_only_add_unlabeled_fragments() -> None:
    # Оценки #70 не меняются: судья #105 размечает только то, чего в наборе ещё нет.
    question = _v2("single", [("a/2", 1), ("a/1", 2)])

    merged = merge_v2_grades(
        question,
        {"a/2": 2, "x/1": 2, "x/2": 1, "x/3": 0},
        dict.fromkeys(("a/2", "x/1", "x/2", "x/3"), FP),
    )

    # Прежние оценки остаются на своих местах: diff набора показывает только новые.
    assert [(r.key, r.grade) for r in merged.relevant] == [
        ("a/2", 1),
        ("a/1", 2),
        ("x/1", 2),
        ("x/2", 1),
    ]


def test_v2_question_without_answer_gets_no_answer_grade() -> None:
    merged = merge_v2_grades(_v2("none", []), {"x/1": 2}, {"x/1": FP})

    assert [(r.key, r.grade) for r in merged.relevant] == [("x/1", 1)]


def test_run_with_errors_cannot_be_compared(tmp_path: Path) -> None:
    path = tmp_path / "answers.json"
    record = {
        "id": "s-001",
        "found": [],
        "shown": [],
        "claims": [],
        "dropped": 0,
        "latency_s": 1.0,
        "stages": {},
        "error": "llm: нет ответа",
        "verdicts": [],
        "points": [],
        "must_not": [],
    }
    path.write_text(json.dumps({"set": "v3", "variant": "base", "errors": 1, "records": [record]}))

    with pytest.raises(ValueError, match="ошибк"):
        load_run(path)


def test_rejudged_record_drops_old_verdicts() -> None:
    # После правки эталона медиком судьи оценивают те же ответы заново (`judge --force`).
    record = {"id": "s-001", "claims": [], "verdicts": ["supported"], "points": [True]}
    record["must_not"] = [False]

    again = unjudged(record)

    assert (again["verdicts"], again["points"], again["must_not"]) == (None, None, None)
    assert again["claims"] == [] and record["points"] == [True]
