"""Правила эскалации сигнала «Обязательно» (спека #56). Чистые функции, без БД.

Шаги идут от `urgent_since` — момента, когда сигнал стал «Обязательно»: 1 — повтор ведущему
врачу, 2 — заместителю, 3 — администраторам. Эскалация идёт, пока сигнал `new`.
Заместитель — по назначению администратора на день по Москве.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from uuid import UUID

# Дни назначения — по московскому календарю, как у сигналов. Летнего времени с 2014 года нет.
MOSCOW = timezone(timedelta(hours=3))
STEPS = (1, 2, 3)
# Не длиннее квартала: назначение — по приказу или графику (спека #56, раздел 3).
MAX_ASSIGNMENT_DAYS = 92
ACCESS_HOURS = 12


class AssignmentError(Exception):
    """Назначение заместителя неверно. Текст видит администратор."""


class AssignmentConflictError(AssignmentError):
    """Период пересекается с действующим назначением того же врача (HTTP 409)."""


@dataclass(frozen=True)
class Assignment:
    """Назначение заместителя: обе даты входят в период."""

    id: UUID
    doctor_id: UUID
    deputy_id: UUID
    starts_on: date
    ends_on: date
    revoked: bool


def due_at(urgent_since: datetime, step: int, minutes: Sequence[int]) -> datetime:
    """Срок шага `step` (1–3) от момента, когда сигнал стал «Обязательно»."""
    return urgent_since + timedelta(minutes=minutes[step - 1])


def escalates(level: str, state: str) -> bool:
    """Эскалация идёт, пока «Обязательно» никто не взял: «в работе» или решение её останавливают."""
    return level == "mandatory" and state == "new"


def steps_to_run(step: int, *, deputy_found: bool) -> list[int]:
    """Шаги, которые выполнить сейчас: без заместителя после шага 2 сразу идёт шаг 3."""
    if step == 2 and not deputy_found:
        return [2, 3]
    return [step]


def active_deputy(assignments: Iterable[Assignment], doctor_id: UUID, day: date) -> UUID | None:
    """Заместитель врача на день: неотозванное назначение, в период которого входит день."""
    for a in assignments:
        if a.doctor_id == doctor_id and not a.revoked and a.starts_on <= day <= a.ends_on:
            return a.deputy_id
    return None


def check_assignment(new: Assignment, existing: Iterable[Assignment]) -> None:
    """Проверить назначение перед записью.

    Raises:
        AssignmentError: Врач сам себе заместитель, конец раньше начала, период длиннее 92 дней.
        AssignmentConflictError: Период пересекается с действующим назначением врача.
    """
    if new.doctor_id == new.deputy_id:
        raise AssignmentError("Врач не может быть сам себе заместителем")
    if new.ends_on < new.starts_on:
        raise AssignmentError("Конец периода раньше начала")
    if (new.ends_on - new.starts_on).days + 1 > MAX_ASSIGNMENT_DAYS:
        raise AssignmentError(f"Период не длиннее {MAX_ASSIGNMENT_DAYS} дней")
    for a in existing:
        if a.doctor_id != new.doctor_id or a.revoked or a.id == new.id:
            continue
        if a.starts_on <= new.ends_on and new.starts_on <= a.ends_on:
            raise AssignmentConflictError(
                f"Период пересекается с назначением {a.starts_on:%d.%m}–{a.ends_on:%d.%m}"
            )


def access_expires_at(granted_at: datetime, ends_on: date) -> datetime:
    """Конец доступа заместителя: 12 часов от выдачи, но не позже конца дня `ends_on`."""
    day_after = datetime.combine(ends_on + timedelta(days=1), time(0, 0), MOSCOW)
    return min(granted_at + timedelta(hours=ACCESS_HOURS), day_after)
