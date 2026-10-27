"""Правила эскалации без БД (спека #56, разделы 2, 4 и 9).

Источник правил — спека #56: шаги через 15, 30 и 60 минут от `urgent_since`, остановка
выходом из `new`, шаг 3 сразу, если заместителя нет; назначение заместителя — период
с обеими датами, не длиннее 92 дней, без пересечений; доступ — 12 часов или до конца
последнего дня назначения.
"""

from datetime import UTC, date, datetime, time, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from structlog.testing import capture_logs

from clin_compass.modules.alerts import chain
from clin_compass.modules.alerts.escalation import (
    MOSCOW,
    Assignment,
    AssignmentError,
    access_expires_at,
    active_deputy,
    check_assignment,
    due_at,
    escalates,
    steps_to_run,
)
from clin_compass.modules.alerts.tasks import Jobs

SINCE = datetime(2026, 10, 18, 9, 0, tzinfo=UTC)
MINUTES = (15, 30, 60)
DOCTOR, DEPUTY, OTHER = uuid4(), uuid4(), uuid4()


@pytest.mark.parametrize(("step", "minutes"), [(1, 15), (2, 30), (3, 60)])
def test_steps_are_counted_from_urgent_since(step: int, minutes: int) -> None:
    assert due_at(SINCE, step, MINUTES) == SINCE + timedelta(minutes=minutes)


@pytest.mark.parametrize(
    ("level", "state", "expected"),
    [
        ("mandatory", "new", True),
        ("mandatory", "in_work", False),  # «Взять в работу» или «Не удалось связаться»
        ("mandatory", "resolved", False),
        ("desirable", "new", False),
    ],
)
def test_escalation_runs_only_for_new_mandatory(level: str, state: str, expected: bool) -> None:
    assert escalates(level, state) is expected


@pytest.mark.parametrize(
    ("step", "deputy_found", "expected"),
    [
        (1, False, [1]),
        (2, True, [2]),
        (2, False, [2, 3]),  # заместителя нет — администратору сразу, на 30-й минуте
        (3, False, [3]),
    ],
)
def test_steps_to_run(step: int, deputy_found: bool, expected: list[int]) -> None:
    assert steps_to_run(step, deputy_found=deputy_found) == expected


def assignment(
    starts: date,
    ends: date,
    *,
    deputy: object = DEPUTY,
    revoked: bool = False,
) -> Assignment:
    return Assignment(
        id=uuid4(),
        doctor_id=DOCTOR,
        deputy_id=deputy,  # type: ignore[arg-type]
        starts_on=starts,
        ends_on=ends,
        revoked=revoked,
    )


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (date(2026, 10, 19), None),  # до начала
        (date(2026, 10, 20), DEPUTY),  # первый день входит
        (date(2026, 10, 26), DEPUTY),  # последний день входит
        (date(2026, 10, 27), None),
    ],
)
def test_active_deputy_on_a_day(day: date, expected: object) -> None:
    rows = [assignment(date(2026, 10, 20), date(2026, 10, 26))]

    assert active_deputy(rows, DOCTOR, day) == expected


def test_revoked_assignment_does_not_act() -> None:
    rows = [assignment(date(2026, 10, 20), date(2026, 10, 26), revoked=True)]

    assert active_deputy(rows, DOCTOR, date(2026, 10, 21)) is None


def test_other_doctors_assignment_does_not_act() -> None:
    rows = [assignment(date(2026, 10, 20), date(2026, 10, 26))]

    assert active_deputy(rows, OTHER, date(2026, 10, 21)) is None


@pytest.mark.parametrize(
    ("starts", "ends", "deputy", "message"),
    [
        (date(2026, 10, 20), date(2026, 10, 26), DOCTOR, "сам себе"),
        (date(2026, 10, 26), date(2026, 10, 20), DEPUTY, "раньше"),
        (date(2026, 10, 1), date(2027, 1, 1), DEPUTY, "92"),
    ],
)
def test_invalid_assignment(starts: date, ends: date, deputy: object, message: str) -> None:
    with pytest.raises(AssignmentError, match=message):
        check_assignment(assignment(starts, ends, deputy=deputy), [])


def test_overlap_with_active_assignment_is_an_error() -> None:
    existing = [assignment(date(2026, 10, 20), date(2026, 10, 26))]

    with pytest.raises(AssignmentError, match="пересекается"):
        check_assignment(assignment(date(2026, 10, 26), date(2026, 10, 30), deputy=OTHER), existing)


def test_revoked_assignment_does_not_block_a_new_one() -> None:
    existing = [assignment(date(2026, 10, 20), date(2026, 10, 26), revoked=True)]

    check_assignment(assignment(date(2026, 10, 21), date(2026, 10, 30), deputy=OTHER), existing)


def test_92_days_with_both_dates_is_allowed() -> None:
    check_assignment(assignment(date(2026, 10, 1), date(2026, 12, 31)), [])


def test_access_lasts_12_hours() -> None:
    granted = datetime.combine(date(2026, 10, 20), time(9, 0), MOSCOW)

    assert access_expires_at(granted, date(2026, 10, 26)) == granted + timedelta(hours=12)


def test_access_ends_with_the_last_day_of_the_assignment() -> None:
    # Выдан в 20:00 последнего дня назначения: действует до полуночи по Москве.
    granted = datetime.combine(date(2026, 10, 26), time(20, 0), MOSCOW)

    assert access_expires_at(granted, date(2026, 10, 26)) == datetime.combine(
        date(2026, 10, 27), time(0, 0), MOSCOW
    )


async def test_failed_escalation_attempt_is_a_warning() -> None:
    """Неудачная попытка — предупреждение; ошибку после последней пишет очередь (#170, 9.8)."""

    def broken_session() -> None:
        raise RuntimeError("db down")

    jobs = Jobs(sessionmaker=broken_session, settings=SimpleNamespace())  # type: ignore[arg-type]
    with capture_logs() as logs, pytest.raises(RuntimeError):
        await jobs.escalate(chain.payload(uuid4(), 1))

    assert [(e["event"], e["log_level"]) for e in logs] == [
        ("alert.escalation_attempt_failed", "warning")
    ]
