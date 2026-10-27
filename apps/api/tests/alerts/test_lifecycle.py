"""Состояния сигнала и переходы без БД (спека #95, разделы 3 и 6).

Источник каждого случая — спека #95, раздел 3, строка таблицы в комментарии над случаем.
"""

from datetime import date, timedelta

import pytest
from clin_compass_knowledge.schemas.signal_decisions import SignalDecisions

from clin_compass.modules.alerts.handling import thresholds
from clin_compass.modules.alerts.lifecycle import (
    ConflictError,
    InvalidError,
    after_acknowledge,
    after_no_contact,
    after_raise,
    check_resolve,
    check_snooze,
    decision_options,
    effective_state,
)
from clin_compass.modules.alerts.rules import RuleBook, repository_knowledge_dir

TODAY = date(2026, 10, 22)
DECISIONS = SignalDecisions.model_validate(
    {
        "version": 1,
        "status": "reviewed",
        "safety": {
            "decisions": [
                {
                    "id": "risk-not-high",
                    "text": "Риск не высокий",
                    "reason_hint": "план",
                    "sources": [{"source": "s", "locator": "1"}],
                }
            ],
            "no_contact": {
                "text": "Не удалось связаться",
                "sources": [{"source": "s", "locator": "1"}],
            },
        },
        "therapy": {
            "decisions": [
                {
                    "id": "drug-stopped",
                    "text": "Препарат отменён",
                    "reason_hint": "что сделано",
                    "applies_to": ["contraindication", "caution", "reaction"],
                },
                {
                    "id": "continued",
                    "text": "Продолжен",
                    "reason_hint": "план",
                    "applies_to": ["caution", "reaction"],
                },
                {
                    "id": "data-corrected",
                    "text": "Данные исправлены",
                    "reason_hint": "что исправлено",
                    "applies_to": ["contraindication"],
                },
            ]
        },
        "referral_decline": {"reasons": [{"id": "other", "text": "Другое"}]},
        "snooze": {"max_days": 14, "presets_days": [3, 7, 14], "basis": "период опроса"},
    }
)


# «Отсрочка истекла»: в день возврата сигнал снова новый.
@pytest.mark.parametrize(
    ("state", "until", "expected"),
    [
        ("snoozed", TODAY + timedelta(days=1), "snoozed"),
        ("snoozed", TODAY, "new"),
        ("snoozed", TODAY - timedelta(days=3), "new"),
        ("in_work", None, "in_work"),
        ("new", None, "new"),
        ("resolved", None, "resolved"),
    ],
)
def test_effective_state(state: str, until: date | None, expected: str) -> None:
    assert effective_state(state, until, TODAY) == expected


# «Взять в работу»: новый и вернувшийся из отсрочки → в работе; повтор ничего не меняет.
@pytest.mark.parametrize(
    ("state", "expected"),
    [("new", "in_work"), ("snoozed", "in_work"), ("in_work", None), ("resolved", None)],
)
def test_acknowledge(state: str, expected: str | None) -> None:
    assert after_acknowledge(state) == expected


# «Отсрочка»: только «Желательно», с завтра и не дальше max_days, не у решённого.
@pytest.mark.parametrize(
    ("level", "state", "days", "error"),
    [
        ("desirable", "new", 1, None),
        ("desirable", "in_work", 14, None),
        ("desirable", "snoozed", 7, None),
        ("desirable", "new", 15, InvalidError),
        ("desirable", "new", 0, InvalidError),
        ("desirable", "new", -1, InvalidError),
        ("mandatory", "new", 3, ConflictError),
        ("desirable", "resolved", 3, ConflictError),
    ],
)
def test_snooze(level: str, state: str, days: int, error: type[Exception] | None) -> None:
    until = TODAY + timedelta(days=days)
    if error is None:
        check_snooze(level, state, until, TODAY, DECISIONS)
    else:
        with pytest.raises(error):
            check_snooze(level, state, until, TODAY, DECISIONS)


def test_snooze_needs_decision_data() -> None:
    # «Сигнал без файла решений»: мини-каталоги knowledge/ в тестах API.
    with pytest.raises(ConflictError):
        check_snooze("desirable", "new", TODAY + timedelta(days=3), TODAY, None)


# «Не удалось связаться»: запись в истории, новый → в работе; только у сигналов `alert`.
@pytest.mark.parametrize(
    ("kind", "state", "expected"),
    [
        ("alert", "new", "in_work"),
        ("alert", "in_work", "in_work"),
        ("alert", "snoozed", "in_work"),
        ("safety", "new", ConflictError),
        ("routing", "new", ConflictError),
        ("alert", "resolved", ConflictError),
    ],
)
def test_no_contact(kind: str, state: str, expected: object) -> None:
    if isinstance(expected, str):
        assert after_no_contact(kind, state) == expected
    else:
        with pytest.raises(ConflictError):
            after_no_contact(kind, state)


# «Подъём уровня»: отложенный сигнал с «Обязательно» снова новый.
@pytest.mark.parametrize(
    ("state", "expected"),
    [("snoozed", "new"), ("new", None), ("in_work", None), ("resolved", None)],
)
def test_raise_unsnoozes(state: str, expected: str | None) -> None:
    assert after_raise(state) == expected


# «Варианты решения»: по виду сигнала и виду сигнала безопасности.
@pytest.mark.parametrize(
    ("kind", "safety_kind", "ids"),
    [
        ("alert", None, ["risk-not-high"]),
        ("safety", "contraindication", ["drug-stopped", "data-corrected"]),
        ("safety", "caution", ["drug-stopped", "continued"]),
        ("safety", "reaction", ["drug-stopped", "continued"]),
        # Сигнал до #95: вида нет — все варианты.
        ("safety", None, ["drug-stopped", "continued", "data-corrected"]),
    ],
)
def test_decision_options(kind: str, safety_kind: str | None, ids: list[str]) -> None:
    assert [o.id for o in decision_options(kind, safety_kind, DECISIONS)] == ids


def test_routing_is_decided_by_referral() -> None:
    # Направление и отказ от него — #35.
    with pytest.raises(ConflictError):
        decision_options("routing", None, DECISIONS)


def test_no_decision_data() -> None:
    with pytest.raises(ConflictError):
        decision_options("alert", None, None)


# «Причина»: обязательна у «Обязательно», вариант — только из списка, решённый — 409.
@pytest.mark.parametrize(
    ("level", "state", "decision_id", "reason", "error"),
    [
        ("mandatory", "in_work", "risk-not-high", "Связались, риск не высокий", None),
        ("mandatory", "new", "risk-not-high", "Связались", None),
        ("mandatory", "in_work", "risk-not-high", "   ", InvalidError),
        ("mandatory", "in_work", "risk-not-high", None, InvalidError),
        ("desirable", "snoozed", "risk-not-high", None, None),
        ("mandatory", "in_work", "drug-stopped", "текст", InvalidError),
        ("mandatory", "resolved", "risk-not-high", "текст", ConflictError),
    ],
)
def test_resolve(
    level: str,
    state: str,
    decision_id: str,
    reason: str | None,
    error: type[Exception] | None,
) -> None:
    options = decision_options("alert", None, DECISIONS)
    if error is None:
        assert check_resolve(level, state, options, decision_id, reason).id == decision_id
    else:
        with pytest.raises(error):
            check_resolve(level, state, options, decision_id, reason)


def test_thresholds_of_the_repository_rules() -> None:
    # Спека #95, раздел 6: линейка PHQ-9 — пороги 10 и 20 правил маршрутизации,
    # шкала ответа пункта 9 — порог 1 правила о суицидальных мыслях.
    rules = RuleBook.load(repository_knowledge_dir()).rules
    found = {(t.rule_id, t.item or t.score, t.op, t.value) for t in thresholds(rules, "phq-9")}

    assert {
        ("routing-depression-probable", "total", ">=", 10),
        ("routing-depression-severe", "total", ">=", 20),
        ("phq9-suicidal-thoughts", "q9", ">=", 1),
    } <= found
