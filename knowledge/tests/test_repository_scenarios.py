"""Баллы из сценариев knowledge/scenarios: срабатывают ровно те правила, что указаны в шаге."""

from pathlib import Path

import pytest

from clin_compass_knowledge.loader import load_knowledge
from clin_compass_knowledge.rules import evaluate

KNOWLEDGE = load_knowledge(Path(__file__).parents[1])

# (сценарий, шаг системы, баллы из предыдущих шагов, правила, которые должны сработать)
CASES = [
    (
        "scenario-1-aviandr",
        "s3",
        {"gad-7": {"total": 14}, "phq-9": {"total": 8, "q9": 0}},
        {"routing-anxiety-probable"},
    ),
    ("scenario-1-aviandr", "s9", {"phq-9": {"q9": 1}}, {"phq9-suicidal-thoughts"}),
    (
        "scenario-2-aviandr-post-covid",
        "s3",
        {"gad-7": {"total": 9}, "phq-9": {"total": 6, "q9": 0}},
        set(),
    ),
    (
        "scenario-3-aviandr-contraindication",
        "s3",
        {"gad-7": {"total": 13}, "phq-9": {"total": 16, "q9": 0}},
        {"routing-anxiety-probable", "routing-depression-probable"},
    ),
]


@pytest.mark.parametrize(("scenario_id", "step_id", "results", "expected"), CASES)
def test_scenario_step_fires_listed_rules(
    scenario_id: str, step_id: str, results: dict[str, dict[str, float]], expected: set[str]
) -> None:
    step = next(s for s in KNOWLEDGE.scenarios[scenario_id].steps if s.id == step_id)
    fired = {rule_id for rule_id, rule in KNOWLEDGE.rules.items() if evaluate(rule, results)}

    assert fired == expected
    assert fired <= set(step.refs.rules), f"шаг {step_id} не ссылается на сработавшие правила"
