"""Расчёт приверженности без БД (спека #34, разделы 3 и 5).

Доза — один приём одного назначения в один день: `(prescription_id, day, slot)`. У приёма
есть время; доза есть, если её время внутри назначения, от `started_at` до конца. Состояние
дозы — последняя отметка; `cleared` и отсутствие отметок значат «нет отметки». Доля —
принятые дозы из запланированных за 14 дней, знаменатель по схеме каждого дня. Доза без
отметки, время которой ещё не пришло, в знаменатель не входит.
"""

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Literal
from uuid import UUID

MarkStatus = Literal["taken", "missed", "cleared"]
DoseStatus = Literal["taken", "missed"]
CourseState = Literal["active", "completed", "stopped", "none"]
DoseKey = tuple[UUID, date, int]

PERIOD_DAYS = 14
# Дни приёма — по московскому календарю, как у назначений. Летнего времени с 2014 года нет.
MOSCOW = timezone(timedelta(hours=3))

# Слова расписания и подпись приёма: то же правило, что `doseSlots` в кабинете врача
# (apps/doctor/src/card/regimen.ts). Начало слова: «внутрь» — не «утро». Время — условное
# время приёма для счёта: с него доза ждёт отметки. Вечер в кабинете пациента — с 17:00 (#31).
_MOMENTS: tuple[tuple[re.Pattern[str], str, time], ...] = (
    (re.compile(r"(^|[\s,])утр"), "Утро", time(8, 0)),
    (re.compile(r"дн[её]м"), "День", time(13, 0)),
    (re.compile(r"вечер"), "Вечер", time(19, 0)),
    (re.compile(r"на ночь|перед сном"), "На ночь", time(22, 0)),
)
# Приёмы без слов в расписании делят этот промежуток поровну.
_FIRST, _LAST = 8 * 60, 20 * 60


@dataclass(frozen=True)
class Course:
    """Назначение глазами приверженности: дозы со временем от `start` до `end`, не включая его.

    `slot_times` — время каждого приёма по Москве. `end` — первое из начала дня `ends_on` и
    `stopped_at`; None — без срока.
    """

    id: UUID
    slot_times: tuple[time, ...]
    start: datetime
    end: datetime | None
    stopped: bool

    @property
    def times_per_day(self) -> int:
        return len(self.slot_times)


@dataclass(frozen=True)
class Mark:
    prescription_id: UUID
    day: date
    slot: int
    status: MarkStatus
    marked_at: datetime


@dataclass(frozen=True)
class Period:
    start: date
    end: date  # включительно
    course: CourseState


@dataclass(frozen=True)
class Dose:
    prescription_id: UUID
    slot: int
    status: DoseStatus | None
    marked_at: datetime | None


@dataclass(frozen=True)
class Day:
    day: date
    doses: list[Dose]


@dataclass(frozen=True)
class Summary:
    days: list[Day]
    planned: int
    taken: int
    missed: int
    unmarked: int
    marked_any: bool


def slot_labels(times_per_day: int, schedule: str) -> list[str]:
    """Подписи приёмов: по словам расписания, если их столько же, сколько приёмов."""
    named = _named(times_per_day, schedule)
    if named:
        return [word for word, _ in named]
    if times_per_day == 1:
        return ["Приём"]
    return [f"Приём {i + 1}" for i in range(times_per_day)]


def slot_times(times_per_day: int, schedule: str) -> tuple[time, ...]:
    """Время приёмов: по словам расписания, как подписи; иначе поровну с 08:00 до 20:00."""
    named = _named(times_per_day, schedule)
    if named:
        return tuple(at for _, at in named)
    if times_per_day == 1:
        return (time(_FIRST // 60),)
    step = (_LAST - _FIRST) // (times_per_day - 1)
    return tuple(time(*divmod(_FIRST + i * step, 60)) for i in range(times_per_day))


def dose_time(course: Course, day: date, slot: int) -> datetime:
    return datetime.combine(day, course.slot_times[slot], MOSCOW)


def has_dose(course: Course, day: date, slot: int) -> bool:
    """Доза есть, если приём есть в схеме и его время внутри назначения."""
    if not 0 <= slot < course.times_per_day:
        return False
    at = dose_time(course, day, slot)
    return course.start <= at and (course.end is None or at < course.end)


def is_due(course: Course, day: date, slot: int, now: datetime) -> bool:
    """Время приёма пришло: пустую дозу можно считать неотмеченной или пропущенной."""
    return dose_time(course, day, slot) <= now


def last_day(course: Course) -> date | None:
    """Последний день с дозой у назначения со сроком. None — без срока или без доз."""
    if course.end is None:
        return None
    day = course.end.astimezone(MOSCOW).date()
    first = course.start.astimezone(MOSCOW).date()
    while day >= first:
        if any(has_dose(course, day, slot) for slot in range(course.times_per_day)):
            return day
        day -= timedelta(days=1)
    return None


def current_marks(marks: Iterable[Mark]) -> dict[DoseKey, Mark]:
    """Последняя отметка каждой дозы; снятая отметка — как её отсутствие."""
    latest: dict[DoseKey, Mark] = {}
    for m in marks:
        key = (m.prescription_id, m.day, m.slot)
        if key not in latest or m.marked_at >= latest[key].marked_at:
            latest[key] = m
    return {key: m for key, m in latest.items() if m.status != "cleared"}


def period_of(courses: Sequence[Course], now: datetime) -> Period:
    """14 дней до сегодня при действующем назначении, иначе — последние 14 дней курса."""
    today = now.astimezone(MOSCOW).date()
    if any(c.start <= now and (c.end is None or now < c.end) for c in courses):
        return _ending(today, "active")
    ended = [(day, c.start, c.stopped) for c in courses if (day := last_day(c)) is not None]
    if not ended:
        return Period(today, today, "none")
    end_day, _, stopped = max(ended)
    return _ending(end_day, "stopped" if stopped else "completed")


def summarize(
    courses: Sequence[Course], marks: Iterable[Mark], period: Period, now: datetime
) -> Summary:
    """Дозы периода по дням и их числа. Назначения — в порядке начала.

    Доза без отметки, время которой не пришло, не показывается и не считается.
    """
    if period.course == "none":
        return Summary([], 0, 0, 0, 0, False)
    state = current_marks(marks)
    ordered = sorted(courses, key=lambda c: c.start)
    days: list[Day] = []
    day = period.start
    while day <= period.end:
        doses = []
        for c in ordered:
            for slot in range(c.times_per_day):
                if not has_dose(c, day, slot):
                    continue
                m = state.get((c.id, day, slot))
                if m is None:
                    if is_due(c, day, slot, now):
                        doses.append(Dose(c.id, slot, None, None))
                else:
                    status: DoseStatus = "taken" if m.status == "taken" else "missed"
                    doses.append(Dose(c.id, slot, status, m.marked_at))
        days.append(Day(day, doses))
        day += timedelta(days=1)
    every = [d for day_ in days for d in day_.doses]
    taken = sum(d.status == "taken" for d in every)
    missed = sum(d.status == "missed" for d in every)
    return Summary(
        days=days,
        planned=len(every),
        taken=taken,
        missed=missed,
        unmarked=len(every) - taken - missed,
        marked_any=taken + missed > 0,
    )


def _ending(end: date, course: CourseState) -> Period:
    return Period(end - timedelta(days=PERIOD_DAYS - 1), end, course)


def _named(times_per_day: int, schedule: str) -> list[tuple[str, time]]:
    """Приёмы по словам расписания; пусто, если слов не столько, сколько приёмов."""
    text = schedule.lower()
    named = [(word, at) for pattern, word, at in _MOMENTS if pattern.search(text)]
    return named if len(named) == times_per_day else []
