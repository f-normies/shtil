"""Проверка условия правила (спека, разделы 4.4 и 6)."""

import pytest

from clin_compass_knowledge.loader import load_knowledge
from clin_compass_knowledge.rules import evaluate
from clin_compass_knowledge.schemas.rule import Condition, Results
from tests.helpers import VALID


def leaf(op: str, value: float) -> Condition:
    return Condition.model_validate({"scale": "s", "item": "q", "op": op, "value": value})


@pytest.mark.parametrize(
    ("op", "value", "answer", "fires"),
    [
        (">=", 2, 2, True),
        (">=", 2, 1, False),
        (">", 2, 2, False),
        (">", 2, 3, True),
        ("<=", 2, 2, True),
        ("<=", 2, 3, False),
        ("<", 2, 1, True),
        ("<", 2, 2, False),
        ("==", 2, 2, True),
        ("==", 2, 1, False),
    ],
)
def test_comparison(op: str, value: float, answer: float, fires: bool) -> None:
    assert evaluate(leaf(op, value), {"s": {"q": answer}}) is fires


def test_missing_data_is_false() -> None:
    assert evaluate(leaf(">=", 0), {}) is False
    assert evaluate(leaf(">=", 0), {"s": {}}) is False


def test_any_and_all() -> None:
    yes, no = leaf(">=", 0), leaf(">=", 99)
    results: Results = {"s": {"q": 1}}

    assert evaluate(Condition(any=(no, yes)), results) is True
    assert evaluate(Condition(any=(no, no)), results) is False
    assert evaluate(Condition(all=(yes, yes)), results) is True
    assert evaluate(Condition(all=(yes, no)), results) is False


def test_leaves_flattens_nested_conditions() -> None:
    a, b, c = leaf(">=", 1), leaf(">=", 2), leaf(">=", 3)
    tree = Condition(all=(a, Condition(any=(b, c))))

    assert tree.leaves() == [a, b, c]
    assert a.leaves() == [a]


def test_score_leaf_reads_score_value() -> None:
    cond = Condition.model_validate({"scale": "s", "score": "total", "op": ">=", "value": 7})

    assert evaluate(cond, {"s": {"total": 7}}) is True


def test_evaluate_accepts_rule() -> None:
    rule = load_knowledge(VALID).rules["example-self-harm"]

    assert evaluate(rule, {"example-mood": {"q3": 1}}) is True


@pytest.mark.parametrize(
    "bad",
    [
        {},
        {"any": []},
        {"any": [{"scale": "s", "item": "q", "op": ">=", "value": 1}], "scale": "s"},
        {"scale": "s", "item": "q", "op": ">="},
        {"scale": "s", "item": "q", "score": "t", "op": ">=", "value": 1},
        {"scale": "s", "op": ">=", "value": 1},
    ],
)
def test_malformed_condition_is_rejected(bad: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        Condition.model_validate(bad)
