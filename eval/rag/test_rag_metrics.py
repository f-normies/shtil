"""Метрики RAG v2 (спека #70, раздел 4)."""

import math

import pytest

from dataset import Question
from rag_metrics import (
    AnswerOutcome,
    answer_diff,
    answer_summary,
    bootstrap_interval,
    compare_runs,
    latency,
    ndcg_at,
    paired_interval,
    recall_at,
    reciprocal_rank,
    retrieval_scores,
    retrieval_summary,
)


def _q(qid: str, kind: str = "single", relevant: list[tuple[str, int]] | None = None) -> Question:
    rel = relevant if relevant is not None else [("a", 2)]
    return Question.model_validate(
        {
            "id": qid,
            "question": "вопрос",
            "kind": kind,
            "split": "dev",
            "origin": [] if kind == "none" else [rel[0][0]],
            "relevant": [{"key": k, "fingerprint": "0123456789ab", "grade": g} for k, g in rel],
        }
    )


@pytest.mark.parametrize(
    ("found", "k", "expected"),
    [(["x", "a", "b"], 1, 0.0), (["x", "a", "b"], 2, 0.5), (["x", "a", "b"], 3, 1.0)],
)
def test_recall_at_k_is_share_of_relevant_in_top_k(
    found: list[str], k: int, expected: float
) -> None:
    assert recall_at({"a", "b"}, found, k) == expected


def test_reciprocal_rank_of_first_relevant() -> None:
    assert reciprocal_rank({"a"}, ["x", "y", "a"]) == pytest.approx(1 / 3)
    assert reciprocal_rank({"a"}, ["x"]) == 0.0


def test_ndcg_uses_grade_as_gain_and_ideal_order() -> None:
    grades = {"a": 2, "b": 1}

    assert ndcg_at(grades, ["a", "b", "x"], 10) == pytest.approx(1.0)
    swapped = (1 + 2 / math.log2(3)) / (2 + 1 / math.log2(3))
    assert ndcg_at(grades, ["b", "a"], 10) == pytest.approx(swapped)
    assert ndcg_at(grades, ["x", "y"], 10) == 0.0


def test_retrieval_scores_of_multi_count_partial_fragments() -> None:
    multi = _q("m", "multi", [("a", 1), ("b", 1)])

    scores = retrieval_scores(multi, ["a", "x", "b"])

    assert scores["recall_at_5"] == 1.0
    assert scores["mrr"] == 1.0


def test_bootstrap_interval_is_reproducible_and_covers_mean() -> None:
    values = [0.0, 1.0] * 20

    def mean(idx: list[int]) -> float:
        return sum(values[i] for i in idx) / len(idx)

    first = bootstrap_interval(len(values), mean, resamples=500, seed=70)
    second = bootstrap_interval(len(values), mean, resamples=500, seed=70)

    assert first == second
    assert first[0] < 0.5 < first[1]


def test_paired_interval_of_identical_configs_is_zero() -> None:
    assert paired_interval([0.2, 0.8, 1.0], [0.2, 0.8, 1.0], resamples=200) == (0.0, 0.0, 0.0)


def test_paired_interval_sees_consistent_gain() -> None:
    base = [0.0] * 30
    better = [1.0] * 25 + [0.0] * 5

    diff, low, high = paired_interval(better, base, resamples=500)

    assert diff == pytest.approx(25 / 30, abs=1e-4)
    assert 0 < low <= diff <= high


def test_retrieval_summary_has_mean_and_interval_per_metric() -> None:
    per_question = [retrieval_scores(_q(f"q{i}"), ["a"] if i % 2 else ["x"]) for i in range(10)]

    summary = retrieval_summary(per_question, resamples=200)

    assert summary["questions"] == 10
    assert summary["recall_at_5"]["mean"] == 0.5
    low, high = summary["recall_at_5"]["ci95"]
    assert low <= 0.5 <= high


def test_answer_summary_counts_supported_and_answered() -> None:
    outcomes = [
        AnswerOutcome(
            _q("q1"),
            cited=("a", "x"),
            verdicts=("supported", "unsupported"),
            dropped=1,
            latency_s=10.0,
            output_tokens=100,
        ),
        AnswerOutcome(
            _q("q2"),
            cited=("x",),
            verdicts=("supported",),
            dropped=0,
            latency_s=20.0,
            output_tokens=200,
        ),
        AnswerOutcome(
            _q("n1", "none", [("a", 1)]),
            cited=(),
            verdicts=(),
            dropped=0,
            latency_s=5.0,
            output_tokens=10,
        ),
    ]

    summary = answer_summary(outcomes, resamples=200)

    assert summary["quotes_found_share"]["mean"] == pytest.approx(3 / 4, abs=1e-4)
    assert summary["supported_share"]["mean"] == pytest.approx(2 / 3, abs=1e-4)
    # Ответ засчитан, только если подтверждённое утверждение ссылается на фрагмент с оценкой.
    assert summary["answered_share"]["mean"] == pytest.approx(1 / 2, abs=1e-4)
    assert summary["empty_on_none_share"]["mean"] == 1.0
    assert summary["latency"] == {"median_s": 10.0, "p95_s": 20.0}
    assert summary["output_tokens_median"] == 100


def test_answer_summary_without_verdicts_leaves_judge_metrics_empty() -> None:
    outcome = AnswerOutcome(
        _q("q1"), cited=("a",), verdicts=(None,), dropped=0, latency_s=1.0, output_tokens=5
    )

    summary = answer_summary([outcome], resamples=50)

    assert summary["supported_share"] is None
    assert summary["answered_share"] is None


def test_latency_median_and_p95() -> None:
    assert latency([3.0, 1.0, 2.0]) == {"median_s": 2.0, "p95_s": 3.0}
    assert latency([]) is None


def _answer(qid: str, verdicts: tuple[str, ...], kind: str = "single") -> AnswerOutcome:
    return AnswerOutcome(
        _q(qid, kind),
        cited=("a",) * len(verdicts),
        verdicts=verdicts,  # type: ignore[arg-type]
        dropped=0,
        latency_s=1.0,
        output_tokens=5,
    )


def test_answer_diff_pairs_runs_by_question() -> None:
    a = [_answer("q1", ("supported",)), _answer("q2", ("supported", "partial"))]
    b = [_answer("q2", ("unsupported",)), _answer("q1", ("supported",))]

    diff = answer_diff(a, b, resamples=200)

    # Подтверждено: 2 из 3 против 1 из 2. С ответом: оба вопроса против одного.
    assert diff["supported_share"][0] == pytest.approx(1 / 6, abs=1e-4)
    assert diff["answered_share"][0] == 0.5
    assert answer_diff(a, a, resamples=200) == {
        "supported_share": (0.0, 0.0, 0.0),
        "answered_share": (0.0, 0.0, 0.0),
    }


def test_answer_diff_rejects_different_questions_and_missing_verdicts() -> None:
    with pytest.raises(ValueError, match="разные вопросы"):
        answer_diff([_answer("q1", ())], [_answer("q2", ())], resamples=10)
    with pytest.raises(ValueError, match="судья"):
        answer_diff([_answer("q1", (None,))], [_answer("q1", ("supported",))], resamples=10)  # type: ignore[arg-type]


def test_compare_runs_marks_runs_equal_to_leader_on_both_metrics() -> None:
    good = [_answer(f"q{n}", ("supported",)) for n in range(30)]
    same = [_answer(f"q{n}", ("supported",)) for n in range(29)] + [_answer("q29", ("partial",))]
    bad = [_answer(f"q{n}", ("unsupported",)) for n in range(30)]

    table = compare_runs({"good": good, "same": same, "bad": bad}, resamples=200)

    assert table["good"]["equal_to_best"] and table["same"]["equal_to_best"]
    assert not table["bad"]["equal_to_best"]
    assert table["bad"]["vs_best"]["supported_share"][0] == -1.0
