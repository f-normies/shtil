"""Расчёт приверженности без БД (спека #34, разделы 3 и 5).

Источник правил — issue #34 и аннотация кадра «Врач v4 · 07 Карточка — приём»: доля —
принятые дозы из запланированных за 14 дней, доза без отметки не принята и не пропущена,
знаменатель по схеме каждого дня, дни до начала назначения не считаются. Время приёма и
границы назначения по часам — ревью PR #158: доза, время которой не пришло, долю не снижает.
"""

from datetime import UTC, date, datetime, time, timedelta
from uuid import uuid4

import pytest

from clin_compass.modules.adherence.doses import (
    MOSCOW,
    Course,
    Mark,
    current_marks,
    has_dose,
    is_due,
    period_of,
    slot_labels,
    slot_times,
    summarize,
)

TODAY = date(2026, 10, 18)
AT = datetime(2026, 10, 18, 9, 0, tzinfo=UTC)
# Конец дня: все дозы сегодня уже наступили.
LATE = datetime.combine(TODAY, time(23, 30), MOSCOW)
AFTERNOON = datetime.combine(TODAY, time(14, 0), MOSCOW)
MORNING_EVENING = (time(8, 0), time(19, 0))


def moscow(day: date, at: time = time(0, 0)) -> datetime:
    return datetime.combine(day, at, MOSCOW)


def course(
    first: date | datetime,
    end: date | datetime | None = None,
    times: tuple[time, ...] = MORNING_EVENING,
    *,
    stopped: bool = False,
) -> Course:
    """Назначение: дата — с начала московских суток, момент — как есть."""
    start = first if isinstance(first, datetime) else moscow(first)
    finish = end if isinstance(end, datetime) or end is None else moscow(end)
    return Course(id=uuid4(), slot_times=times, start=start, end=finish, stopped=stopped)


def mark(c: Course, day: date, slot: int, status: str, at: datetime = AT) -> Mark:
    return Mark(prescription_id=c.id, day=day, slot=slot, status=status, marked_at=at)  # type: ignore[arg-type]


# Те же случаи, что у `doseSlots` в apps/doctor/src/card/regimen.test.ts: правило одно.
@pytest.mark.parametrize(
    ("times", "schedule", "labels"),
    [
        (2, "утром и вечером", ["Утро", "Вечер"]),
        (1, "вечером", ["Вечер"]),
        (1, "на ночь", ["На ночь"]),
        (3, "утром, днём и вечером", ["Утро", "День", "Вечер"]),
        (2, "время приёма определяет врач", ["Приём 1", "Приём 2"]),
        (1, "время приёма определяет врач", ["Приём"]),
        (2, "вечером", ["Приём 1", "Приём 2"]),
        (2, "внутрь вечером и на ночь", ["Вечер", "На ночь"]),
    ],
)
def test_slot_labels_follow_the_therapy_rule(times: int, schedule: str, labels: list[str]) -> None:
    assert slot_labels(times, schedule) == labels


# Время приёма — по слову расписания; без слов приёмы делят 08:00–20:00 поровну.
@pytest.mark.parametrize(
    ("times", "schedule", "expected"),
    [
        (2, "утром и вечером", ["08:00", "19:00"]),
        (1, "на ночь", ["22:00"]),
        (3, "утром, днём и вечером", ["08:00", "13:00", "19:00"]),
        (1, "время приёма определяет врач", ["08:00"]),
        (2, "время приёма определяет врач", ["08:00", "20:00"]),
        (3, "время приёма определяет врач", ["08:00", "14:00", "20:00"]),
    ],
)
def test_slot_times_follow_the_labels(times: int, schedule: str, expected: list[str]) -> None:
    assert [t.strftime("%H:%M") for t in slot_times(times, schedule)] == expected


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (date(2026, 10, 4), False),  # до начала
        (date(2026, 10, 5), True),  # день начала входит
        (date(2026, 10, 18), True),
        (date(2026, 10, 19), False),  # день ends_on не входит
    ],
)
def test_dose_days_run_from_start_to_end_exclusive(day: date, expected: bool) -> None:
    assert has_dose(course(date(2026, 10, 5), date(2026, 10, 19)), day, 0) is expected


def test_dose_days_without_end_go_on() -> None:
    assert has_dose(course(date(2026, 10, 5)), date(2027, 1, 1), 1)


def test_start_day_has_only_doses_after_the_start() -> None:
    # Ревью PR #158: назначение подтверждено в 19:00 — утренней дозы в этот день нет.
    c = course(moscow(date(2026, 10, 5), time(18, 30)))

    assert not has_dose(c, date(2026, 10, 5), 0)
    assert has_dose(c, date(2026, 10, 5), 1)


def test_stop_day_keeps_doses_before_the_stop() -> None:
    c = course(date(2026, 10, 1), moscow(TODAY, time(12, 0)), stopped=True)

    assert has_dose(c, TODAY, 0)
    assert not has_dose(c, TODAY, 1)


def test_dose_is_due_from_its_time() -> None:
    c = course(date(2026, 10, 5))

    assert is_due(c, TODAY, 0, AFTERNOON)
    assert not is_due(c, TODAY, 1, AFTERNOON)


def test_last_mark_wins_and_cleared_means_no_mark() -> None:
    c = course(date(2026, 10, 5))
    day = date(2026, 10, 10)
    marks = [
        mark(c, day, 0, "taken", AT),
        mark(c, day, 0, "cleared", AT + timedelta(minutes=1)),
        mark(c, day, 1, "missed", AT),
        mark(c, day, 1, "taken", AT + timedelta(minutes=1)),
    ]

    state = current_marks(marks)

    assert (c.id, day, 0) not in state
    assert state[(c.id, day, 1)].status == "taken"


def test_period_with_active_course_ends_today() -> None:
    p = period_of([course(date(2026, 10, 1))], LATE)

    assert (p.start, p.end, p.course) == (date(2026, 10, 5), TODAY, "active")


def test_period_of_completed_course_is_its_last_14_days() -> None:
    # Issue #34: курс завершён — доля за последние 14 дней курса.
    p = period_of([course(date(2026, 9, 1), date(2026, 9, 29))], LATE)

    assert (p.start, p.end, p.course) == (date(2026, 9, 15), date(2026, 9, 28), "completed")


def test_period_of_stopped_course_says_stopped() -> None:
    p = period_of([course(date(2026, 9, 1), date(2026, 10, 10), stopped=True)], LATE)

    assert (p.end, p.course) == (date(2026, 10, 9), "stopped")


def test_period_of_course_stopped_at_noon_ends_on_the_stop_day() -> None:
    # Утренняя доза дня отмены была: период кончается этим днём.
    stop = moscow(date(2026, 10, 10), time(12, 0))

    p = period_of([course(date(2026, 9, 1), stop, stopped=True)], LATE)

    assert p.end == date(2026, 10, 10)


def test_period_without_courses_is_none() -> None:
    assert period_of([], LATE).course == "none"


def two_weeks(c: Course) -> list[Mark]:
    """Кадр 07: 5–18 октября, все дозы приняты, кроме вечера 9.10 и 14.10."""
    marks = []
    for i in range(14):
        day = date(2026, 10, 5) + timedelta(days=i)
        marks.append(mark(c, day, 0, "taken"))
        missed = day in (date(2026, 10, 9), date(2026, 10, 14))
        marks.append(mark(c, day, 1, "missed" if missed else "taken"))
    return marks


def test_frame_07_gives_26_of_28() -> None:
    c = course(date(2026, 10, 5))
    p = period_of([c], LATE)

    s = summarize([c], two_weeks(c), p, LATE)

    assert (s.planned, s.taken, s.missed, s.unmarked, s.marked_any) == (28, 26, 2, 0, True)
    assert len(s.days) == 14


def test_dose_without_mark_is_neither_taken_nor_missed() -> None:
    c = course(date(2026, 10, 5))
    marks = [m for m in two_weeks(c) if m.day != TODAY]

    s = summarize([c], marks, period_of([c], LATE), LATE)

    assert (s.planned, s.taken, s.missed, s.unmarked) == (28, 24, 2, 2)


def test_today_without_marks_before_their_time_does_not_lower_the_share() -> None:
    # Ревью PR #158: в 14:00 вечерняя доза ещё впереди — её нет в знаменателе.
    c = course(date(2026, 10, 5))
    marks = [m for m in two_weeks(c) if (m.day, m.slot) != (TODAY, 1)]

    s = summarize([c], marks, period_of([c], AFTERNOON), AFTERNOON)

    assert (s.planned, s.taken, s.missed, s.unmarked) == (27, 25, 2, 0)
    assert len(s.days[-1].doses) == 1


def test_dose_marked_before_its_time_counts() -> None:
    c = course(date(2026, 10, 5))

    s = summarize([c], two_weeks(c), period_of([c], AFTERNOON), AFTERNOON)

    assert (s.planned, s.taken) == (28, 26)


def test_due_dose_without_mark_counts_as_unmarked() -> None:
    c = course(date(2026, 10, 5))
    marks = [m for m in two_weeks(c) if m.day != TODAY]

    s = summarize([c], marks, period_of([c], AFTERNOON), AFTERNOON)

    assert (s.planned, s.unmarked) == (27, 1)


def test_course_started_inside_the_period_counts_from_its_start() -> None:
    # Дни до начала без доз: знаменатель меньше 28.
    c = course(date(2026, 10, 12))

    s = summarize([c], [], period_of([c], LATE), LATE)

    assert s.planned == 14
    assert s.days[0].doses == []
    assert len(s.days[7].doses) == 2


def test_course_started_in_the_evening_skips_that_morning() -> None:
    c = course(moscow(date(2026, 10, 12), time(18, 30)))

    s = summarize([c], [], period_of([c], LATE), LATE)

    assert s.planned == 13
    assert [d.slot for d in s.days[7].doses] == [1]


def test_regimen_change_inside_the_period_counts_each_day_by_its_regimen() -> None:
    once = course(date(2026, 9, 1), date(2026, 10, 12), times=(time(8, 0),), stopped=True)
    twice = course(date(2026, 10, 12))

    s = summarize([once, twice], [], period_of([once, twice], LATE), LATE)

    assert s.planned == 7 * 1 + 7 * 2


def test_regimen_change_at_noon_keeps_the_morning_mark() -> None:
    # Ревью PR #158: утром отмечено 20 мг, в полдень врач перевёл на 40 мг.
    noon = moscow(TODAY, time(12, 0))
    old = course(date(2026, 10, 1), noon, stopped=True)
    new = course(noon)
    morning = mark(old, TODAY, 0, "taken", moscow(TODAY, time(8, 40)))

    s = summarize([old, new], [morning], period_of([old, new], AFTERNOON), AFTERNOON)

    today = s.days[-1].doses
    assert [(d.prescription_id, d.slot, d.status) for d in today] == [(old.id, 0, "taken")]


def test_no_marks_in_the_period() -> None:
    c = course(date(2026, 10, 5))

    s = summarize([c], [], period_of([c], LATE), LATE)

    assert (s.marked_any, s.unmarked) == (False, 28)


def test_late_mark_stays_on_the_dose_day() -> None:
    c = course(date(2026, 10, 5))
    late = AT + timedelta(days=2)

    s = summarize([c], [mark(c, date(2026, 10, 10), 1, "taken", late)], period_of([c], LATE), LATE)

    dose = next(d for d in s.days if d.day == date(2026, 10, 10)).doses[1]
    assert (dose.status, dose.marked_at) == ("taken", late)


def test_mark_outside_dose_days_is_ignored() -> None:
    c = course(date(2026, 10, 12))

    s = summarize([c], [mark(c, date(2026, 10, 6), 0, "taken")], period_of([c], LATE), LATE)

    assert (s.taken, s.marked_any) == (0, False)


def test_ids_are_kept_on_doses() -> None:
    c = course(date(2026, 10, 5))

    s = summarize([c], [], period_of([c], LATE), LATE)

    assert {d.prescription_id for day in s.days for d in day.doses} == {c.id}
