"""Расчёт плановых дат шкалы: табличные случаи правила из спеки #111."""

from datetime import UTC, date, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from clin_compass.modules.assessments.schedule import Answer, Planned, due_on, local_day, plan

R = date(2026, 10, 1)  # день регистрации
P = 14  # period_days PHQ-9 и GAD-7 (#93, п. 4)
G = 3  # grace_days (#93, п. 4)
A1 = UUID(int=1)
A2 = UUID(int=2)


def day(offset: int) -> date:
    return R + timedelta(days=offset)


def done(offset: int, answer: UUID) -> Planned:
    return Planned(day(offset), "done", answer)


def missed(offset: int) -> Planned:
    return Planned(day(offset), "missed", None)


def due(offset: int) -> Planned:
    return Planned(day(offset), "due", None)


def upcoming(offset: int) -> Planned:
    return Planned(day(offset), "upcoming", None)


# (id, источник, ответы: (день, id), сегодня, ожидаемые плановые даты, день опроса пациента)
CASES = [
    (
        "registration_day_is_due",
        "спека #111, правило 1",
        [],
        0,
        [due(0), upcoming(14)],
        0,
    ),
    (
        "last_grace_day_is_still_due",
        "спека #111, правило 3; #93 п. 4",
        [],
        3,
        [due(0), upcoming(14)],
        0,
    ),
    (
        "missed_after_grace",
        "спека #111, правило 3; #93 п. 4",
        [],
        4,
        [missed(0), upcoming(14)],
        0,
    ),
    (
        "missed_dates_follow_period",
        "спека #111, правило 2",
        [],
        30,
        [missed(0), missed(14), due(28), upcoming(42)],
        28,
    ),
    (
        "answer_on_planned_day",
        "спека #111, правила 4 и 6",
        [(0, A1)],
        1,
        [done(0, A1), upcoming(14)],
        14,
    ),
    (
        "answer_on_last_grace_day",
        "спека #111, правила 3 и 6",
        [(3, A1)],
        3,
        [done(0, A1), upcoming(17)],
        17,
    ),
    (
        "next_planned_day_is_due",
        "спека #111, правило 6",
        [(1, A1)],
        15,
        [done(0, A1), due(15), upcoming(29)],
        15,
    ),
    (
        "late_answer_closes_last_gap",
        "спека #111, правило 4; #93 п. 4",
        [(5, A1)],
        6,
        [done(0, A1), upcoming(19)],
        19,
    ),
    (
        "late_answer_keeps_earlier_gaps",
        "спека #111, правило 4",
        [(20, A1)],
        21,
        [missed(0), done(14, A1), upcoming(34)],
        34,
    ),
    (
        "early_answer_shifts_chain",
        "спека #111, правила 5 и 7; #93 п. 4: внеплановый опрос",
        [(0, A1), (6, A2)],
        7,
        [done(0, A1), upcoming(20)],
        20,
    ),
    (
        "two_answers_on_one_day",
        "спека #111, правило 7",
        [(0, A1), (0, A2)],
        1,
        [done(0, A1), upcoming(14)],
        14,
    ),
    (
        "answer_before_registration_is_early",
        "спека #111, правило 5",
        [(-1, A1)],
        0,
        [upcoming(13)],
        13,
    ),
]


@pytest.mark.parametrize(
    ("answers", "today", "expected", "patient_day"),
    [c[2:] for c in CASES],
    ids=[c[0] for c in CASES],
)
def test_plan(
    answers: list[tuple[int, UUID]], today: int, expected: list[Planned], patient_day: int
) -> None:
    planned = plan(
        R,
        [Answer(day(offset), answer_id) for offset, answer_id in answers],
        period_days=P,
        grace_days=G,
        today=day(today),
    )

    assert planned == expected
    assert due_on(planned) == day(patient_day)


@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        (datetime(2026, 10, 1, 20, 30, tzinfo=UTC), date(2026, 10, 1)),  # 23:30 по Москве
        (datetime(2026, 10, 1, 21, 30, tzinfo=UTC), date(2026, 10, 2)),  # 00:30 по Москве
    ],
)
def test_day_is_taken_in_configured_timezone(moment: datetime, expected: date) -> None:
    # Спека #111, «Календарный день»: даты правила — по московскому времени.
    assert local_day(moment, ZoneInfo("Europe/Moscow")) == expected
