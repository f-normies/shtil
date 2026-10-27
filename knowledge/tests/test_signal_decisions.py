"""Решения врача по сигналам: `signal-decisions.yaml` (спека #93)."""

from pathlib import Path
from typing import Any

import pytest

from clin_compass_knowledge.loader import load_knowledge
from tests.helpers import VALID, Edit, messages

FILE = "signal-decisions.yaml"


def test_signal_decisions_are_loaded() -> None:
    decisions = load_knowledge(VALID).signal_decisions

    assert decisions is not None
    assert [d.id for d in decisions.safety.decisions] == ["high-risk", "not-high"]
    assert decisions.snooze.max_days == 14
    assert [(d.id, d.applies_to) for d in decisions.therapy.decisions] == [
        ("drug-stopped", ("contraindication", "caution", "reaction")),
        ("continued", ("caution", "reaction")),
        ("data-corrected", ("contraindication",)),
    ]
    assert decisions.therapy.decisions[2].sources == ()


def test_missing_file_is_not_an_error(tree: Path) -> None:
    # Мини-каталоги knowledge/ в тестах API обходятся без этого файла.
    (tree / FILE).unlink()

    assert messages(tree) == []
    assert load_knowledge(tree).signal_decisions is None


def test_empty_file_is_reported(tree: Path) -> None:
    (tree / FILE).write_text("", encoding="utf-8")

    assert messages(tree) == [f"{FILE}: файл пустой"]


# (правка данных, ожидаемая ошибка). Источник случая — спека #93, раздел «Схема и проверки».
CASES: list[tuple[str, Any, str]] = [
    (
        "safety_decision_needs_source",
        lambda d: d["safety"]["decisions"][0].update(sources=[]),
        "safety.decisions[0].sources: минимум элементов в списке — 1, сейчас 0",
    ),
    (
        "no_contact_needs_source",
        lambda d: d["safety"]["no_contact"].update(sources=[]),
        "safety.no_contact.sources: минимум элементов в списке — 1, сейчас 0",
    ),
    (
        "safety_decision_ids_are_unique",
        lambda d: d["safety"]["decisions"][1].update(id="high-risk"),
        "safety.decisions[1].id: повторяется high-risk",
    ),
    (
        "reason_ids_are_unique",
        lambda d: d["referral_decline"]["reasons"][1].update(id="declined"),
        "referral_decline.reasons[1].id: повторяется declined",
    ),
    (
        "other_reason_is_required",
        lambda d: d["referral_decline"]["reasons"].pop(),
        "referral_decline.reasons: нужна причина other («Другое»)",
    ),
    (
        "presets_ascend",
        lambda d: d["snooze"].update(presets_days=[7, 3]),
        "snooze.presets_days: варианты идут строго по возрастанию",
    ),
    (
        "presets_within_limit",
        lambda d: d["snooze"].update(presets_days=[3, 21]),
        "snooze.presets_days: 21 больше предела max_days 14",
    ),
    (
        "presets_positive",
        lambda d: d["snooze"].update(presets_days=[0, 7]),
        "snooze.presets_days[0]: нужно не меньше 1",
    ),
    (
        "source_in_manifest",
        lambda d: d["referral_decline"]["reasons"][0]["sources"][0].update(source="nope"),
        "referral_decline.reasons[0].sources[0].source: нет источника nope в манифесте",
    ),
    (
        "safety_decision_needs_primary_source",
        lambda d: d["safety"]["decisions"][0]["sources"][0].update(source="example-international"),
        "safety.decisions[0].sources: только дополнительные источники: нужен основной",
    ),
    (
        "no_contact_needs_primary_source",
        lambda d: d["safety"]["no_contact"]["sources"][0].update(source="example-international"),
        "safety.no_contact.sources: только дополнительные источники: нужен основной",
    ),
    (
        "reason_needs_primary_source",
        lambda d: d["referral_decline"]["reasons"][0]["sources"][0].update(
            source="example-international"
        ),
        "referral_decline.reasons[0].sources: только дополнительные источники: нужен основной",
    ),
    (
        "therapy_decision_ids_are_unique",
        lambda d: d["therapy"]["decisions"][1].update(id="drug-stopped"),
        "therapy.decisions[1].id: повторяется drug-stopped",
    ),
    (
        "therapy_kind_needs_a_decision",
        lambda d: [x.update(applies_to=["caution"]) for x in d["therapy"]["decisions"][:2]],
        "therapy.decisions: нет варианта для reaction",
    ),
    (
        "therapy_decision_applies_somewhere",
        lambda d: d["therapy"]["decisions"][0].update(applies_to=[]),
        "therapy.decisions[0].applies_to: минимум элементов в списке — 1, сейчас 0",
    ),
    (
        "therapy_source_needs_primary",
        lambda d: d["therapy"]["decisions"][0]["sources"][0].update(source="example-international"),
        "therapy.decisions[0].sources: только дополнительные источники: нужен основной",
    ),
    (
        "extra_field_is_reported",
        lambda d: d["referral_decline"].update(comment_required=True),
        "referral_decline.comment_required: лишнее поле",
    ),
]


@pytest.mark.parametrize(("change", "message"), [c[1:] for c in CASES], ids=[c[0] for c in CASES])
def test_signal_decisions_check(tree: Path, edit: Edit, change: Any, message: str) -> None:
    edit(FILE, change)

    assert messages(tree) == [f"{FILE}: {message}"]


def test_therapy_kind_is_known(tree: Path, edit: Edit) -> None:
    edit(FILE, lambda d: d["therapy"]["decisions"][0].update(applies_to=["allergy"]))

    expected = "допустимые значения: 'contraindication', 'caution' или 'reaction'"
    assert f"{FILE}: therapy.decisions[0].applies_to[0]: {expected}" in messages(tree)
