"""Метрики набора v3 и правило выбора (спека #92, раздел 4)."""

from typing import Any

import pytest

from dataset_v3 import QuestionV3
from v3_metrics import OutcomeV3, breakdown, point_losses, question_values, select, summary_v3

FP = "0123456789ab"


def _question(qid: str = "s-001", **overrides: Any) -> QuestionV3:
    raw: dict[str, Any] = {
        "id": qid,
        "question": "вопрос",
        "type": "reference",
        "asker": "терапевт",
        "style": "short",
        "kind": "multi",
        "split": "dev",
        "points": [
            {"text": "П1", "critical": True, "evidence": [{"key": "a/1", "fingerprint": FP}]},
            {"text": "П2", "critical": False, "evidence": [{"key": "a/2", "fingerprint": FP}]},
        ],
        "must_not": ["Опасное"],
        "relevant": [
            {"key": "a/1", "fingerprint": FP, "grade": 2},
            {"key": "a/2", "fingerprint": FP, "grade": 2},
            {"key": "a/3", "fingerprint": FP, "grade": 1},
        ],
    }
    raw.update(overrides)
    return QuestionV3.model_validate(raw)


def _none(qid: str = "s-090") -> QuestionV3:
    return _question(qid, type="none", kind="none", points=[], must_not=[], relevant=[])


def _outcome(question: QuestionV3, **overrides: Any) -> OutcomeV3:
    values: dict[str, Any] = {
        "question": question,
        "found": ("a/1", "x/1", "a/3", "x/2", "x/3", "a/2"),
        "shown": ("a/1", "x/1", "a/3", "x/2", "x/3"),
        "cited": ("a/1", "a/1"),
        "claim_verdicts": ("supported", "partial"),
        "points": (True, False),
        "must_not": (False,),
        "dropped": 1,
        "latency_s": 10.0,
        "stages": {"answer": 9.0, "rewrite": 1.0},
    }
    values.update(overrides)
    return OutcomeV3(**values)


def test_question_values_cover_search_context_answer_and_safety() -> None:
    values = question_values(_outcome(_question()))

    assert values["recall_at_5"] == pytest.approx(2 / 3)
    assert values["recall_at_20"] == 1.0
    assert values["hit_at_5"] == 1.0
    assert values["mrr"] == 1.0
    assert values["precision_at_5"] == pytest.approx(2 / 5)
    assert values["context_coverage"] == 0.5
    assert values["context_use"] == pytest.approx(1 / 5)
    assert values["point_coverage"] == 0.5
    assert values["critical_complete"] == 1.0
    assert values["must_not_share"] == 0.0
    assert values["false_refusal"] == 0.0
    assert values["refusal"] is None


def test_empty_answer_is_refusal_and_has_no_context_use() -> None:
    empty = {"cited": (), "claim_verdicts": (), "points": (), "must_not": ()}

    none = question_values(_outcome(_none(), shown=(), **empty))
    answerable = question_values(
        _outcome(_question(), **{**empty, "points": (False, False), "must_not": (False,)})
    )

    assert none["refusal"] == 1.0
    assert none["recall_at_5"] is None and none["point_coverage"] is None
    assert none["context_use"] is None
    assert answerable["false_refusal"] == 1.0
    assert answerable["critical_complete"] == 0.0


def test_summary_has_means_supported_share_and_stage_latency() -> None:
    outcomes = [_outcome(_question("s-001")), _outcome(_question("s-002"), points=(True, True))]

    summary = summary_v3(outcomes, resamples=200)

    assert summary["questions"] == 2
    assert summary["point_coverage"]["mean"] == 0.75
    assert summary["supported_share"]["mean"] == 0.5
    assert summary["latency"]["total"] == {"median_s": 10.0, "p95_s": 10.0}
    assert summary["latency"]["rewrite"] == {"median_s": 1.0, "p95_s": 1.0}


def test_unjudged_runs_have_no_judge_metrics() -> None:
    outcome = _outcome(_question(), points=None, must_not=None, claim_verdicts=(None, None))

    summary = summary_v3([outcome], resamples=200)

    assert summary["point_coverage"] is None
    assert summary["supported_share"] is None
    assert summary["recall_at_5"]["mean"] == pytest.approx(0.6667)


def test_breakdown_by_type_and_style() -> None:
    outcomes = [
        _outcome(_question("s-001")),
        _outcome(_question("s-002", type="case", style="full"), points=(True, True)),
    ]

    by_type = breakdown(outcomes, "type")

    assert by_type["reference"]["point_coverage"] == 0.5
    assert by_type["case"]["point_coverage"] == 1.0
    assert set(breakdown(outcomes, "style")) == {"short", "full"}


def _run(coverage: list[tuple[bool, bool]], latency: float, **overrides: Any) -> list[OutcomeV3]:
    return [
        _outcome(_question(f"s-{n:03}"), points=points, latency_s=latency, **overrides)
        for n, points in enumerate(coverage)
    ]


def test_select_takes_fastest_variant_equal_to_leader() -> None:
    half = [(True, False)] * 10
    runs = {
        "base": _run(half, 8.0),
        "slow": _run([(True, True), *half[1:]], 14.0),
        "worse": _run([(False, False)] * 10, 5.0),
    }

    table = select(runs, resamples=300)

    assert table["leader"] == "slow"
    assert table["runs"]["base"]["candidate"] is True
    assert table["runs"]["worse"]["candidate"] is False
    assert table["winner"] == "base"


def test_select_rejects_candidate_with_significant_safety_loss() -> None:
    same = [(True, False)] * 10
    runs = {
        "safe": _run(same, 10.0),
        "unsafe": _run(same, 5.0, must_not=(True,)),
    }

    table = select(runs, resamples=300)

    assert table["runs"]["unsafe"]["candidate"] is False
    assert table["winner"] == "safe"


def test_select_leaves_choice_to_human_when_no_candidate_fits_budget() -> None:
    runs = {"slow": _run([(True, False)] * 4, 30.0)}

    assert select(runs, resamples=200)["winner"] is None


def test_select_rejects_unjudged_runs() -> None:
    judged = _run([(True, False)] * 3, 5.0)
    unjudged = [_outcome(o.question, points=None, must_not=None) for o in judged]

    with pytest.raises(ValueError, match="судья"):
        select({"judged": judged, "unjudged": unjudged}, resamples=100)


def test_point_losses_split_by_where_the_fragment_was() -> None:
    outcome = _outcome(
        _question(),
        found=("x/1", "a/2"),
        shown=("a/1",),
        points=(True, False),
    )

    losses = point_losses([outcome, _outcome(_question("s-002"), points=None)])

    assert losses == {
        "all": {"shown_said": 1, "top20_not_said": 1},
        "reference": {"shown_said": 1, "top20_not_said": 1},
    }
