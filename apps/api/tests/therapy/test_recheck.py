"""Повторная проверка назначения без БД (спека #29, разделы 3 и 8).

Сигнал поднимается, только если статус правила стал хуже, чем в `safety_state` назначения:
`pass`/`manual` → `warn` или `fail`, `warn` → `fail`. Новый препарат без МНН — «желательно».
"""

import pytest

from clin_compass.modules.therapy.checks import Check, CheckStatus
from clin_compass.modules.therapy.safety import safety_kind, signal_rule_id, worsened


def check(check_id: str, status: CheckStatus) -> Check:
    return Check(check_id, "Правило", status, "", None)


@pytest.mark.parametrize(
    ("state", "check_id", "status", "signal"),
    [
        # Правило стало хуже: сигнал с уровнем по статусу.
        ({}, "contra:maoi", "fail", ("maoi", "mandatory")),
        ({"contra:renal": "pass"}, "contra:renal", "fail", ("renal", "mandatory")),
        ({"contra:renal": "manual"}, "contra:renal", "fail", ("renal", "mandatory")),
        ({}, "caution:serotonergic", "warn", ("serotonergic", "desirable")),
        ({"caution:x": "manual"}, "caution:x", "warn", ("x", "desirable")),
        # Известно с назначения или с прошлой проверки: сигнала нет.
        ({"contra:maoi": "fail"}, "contra:maoi", "fail", None),
        ({"caution:serotonergic": "warn"}, "caution:serotonergic", "warn", None),
        # Нехватка данных не хуже `pass`: врач уже видел вопрос при подборе.
        ({"contra:renal": "pass"}, "contra:renal", "manual", None),
        ({"contra:renal": "fail"}, "contra:renal", "pass", None),
        # Препарат без МНН: новый — «желательно», известный — без сигнала.
        ({}, "unmatched:деприм", "manual", ("unmatched:деприм", "desirable")),
        ({"unmatched:деприм": "manual"}, "unmatched:деприм", "manual", None),
    ],
)
def test_signal_only_when_a_rule_gets_worse(
    state: dict[str, str],
    check_id: str,
    status: CheckStatus,
    signal: tuple[str, str] | None,
) -> None:
    new_state, signals = worsened(state, [check(check_id, status)])

    assert new_state[check_id] == status
    assert [(s.rule_id, s.level) for s in signals] == ([signal] if signal else [])


def test_state_keeps_rules_that_were_not_checked_again() -> None:
    new_state, _ = worsened({"unmatched:старый": "manual"}, [check("contra:maoi", "pass")])

    assert new_state == {"unmatched:старый": "manual", "contra:maoi": "pass"}


def test_rule_that_recovered_and_worsened_again_signals_again() -> None:
    state, first = worsened({}, [check("contra:renal", "fail")])
    state, recovered = worsened(state, [check("contra:renal", "pass")])
    _, again = worsened(state, [check("contra:renal", "fail")])

    assert [len(first), len(recovered), len(again)] == [1, 0, 1]


def test_old_prescription_without_state_signals_every_known_risk() -> None:
    # Назначение до #29 проверок безопасности не проходило: состояние пустое, и всё, что
    # сработает сейчас, врач видит впервые.
    _, signals = worsened({}, [check("contra:maoi", "fail"), check("caution:sedative", "warn")])

    assert [s.rule_id for s in signals] == ["maoi", "sedative"]


def test_signal_rule_id_keeps_short_ids() -> None:
    assert signal_rule_id("aviandr", "unmatched:деприм") == "aviandr/unmatched:деприм"


def test_long_names_without_inn_do_not_merge_into_one_signal() -> None:
    # Колонка `alerts.rule_id` — 200 символов, название препарата — до 200.
    first = signal_rule_id("aviandr", "unmatched:" + "а" * 195 + "один")
    second = signal_rule_id("aviandr", "unmatched:" + "а" * 195 + "два")

    assert first != second
    assert max(len(first), len(second)) <= 200
    assert first == signal_rule_id("aviandr", "unmatched:" + "а" * 195 + "один")


@pytest.mark.parametrize(
    ("check_id", "kind"),
    [
        ("contra:maoi", "contraindication"),
        ("caution:serotonergic", "caution"),
        # Препарат без МНН: взаимодействия не проверены — как мера предосторожности.
        ("unmatched:деприм", "caution"),
    ],
)
def test_signal_carries_its_safety_kind(check_id: str, kind: str) -> None:
    # Спека #95, раздел 3: по виду сигнала врачу показываются варианты решения.
    assert safety_kind(check_id) == kind
