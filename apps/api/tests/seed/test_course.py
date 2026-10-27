"""Отметки доз назначения сида (спека #34, раздел 8). Без БД.

Все дозы приняты, кроме пропущенных вечерних и утренних без отметки. Утро — 08:40,
вечер — 21:05 по Москве. Сегодня отмечено только утро, и только если 08:40 уже прошло.
"""

from datetime import UTC, date, datetime

from clin_compass.seed.data import SeedCourse
from clin_compass.seed.run import course_marks

# 12:00 UTC — 15:00 по Москве 18.10.
NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
COURSE = SeedCourse(days_ago=5, missed_evenings=(3,), unmarked_mornings=(2,))


def test_course_marks_follow_the_plan() -> None:
    marks = {(m.day, m.slot): m.status for m in course_marks(COURSE, 2, NOW)}

    assert marks[(date(2026, 10, 13), 0)] == "taken"  # первый день курса
    assert marks[(date(2026, 10, 15), 1)] == "missed"
    assert (date(2026, 10, 16), 0) not in marks
    assert marks[(date(2026, 10, 18), 0)] == "taken"
    assert (date(2026, 10, 18), 1) not in marks
    assert len(marks) == 6 * 2 - 1 - 1


def test_marks_are_made_at_morning_and_evening_moscow_time() -> None:
    marks = {(m.day, m.slot): m.marked_at for m in course_marks(COURSE, 2, NOW)}

    assert marks[(date(2026, 10, 14), 0)] == datetime(2026, 10, 14, 5, 40, tzinfo=UTC)
    assert marks[(date(2026, 10, 14), 1)] == datetime(2026, 10, 14, 18, 5, tzinfo=UTC)


def test_today_morning_is_not_marked_before_it_comes() -> None:
    early = datetime(2026, 10, 18, 4, 0, tzinfo=UTC)  # 07:00 по Москве

    marks = {(m.day, m.slot) for m in course_marks(COURSE, 2, early)}

    assert (date(2026, 10, 18), 0) not in marks
