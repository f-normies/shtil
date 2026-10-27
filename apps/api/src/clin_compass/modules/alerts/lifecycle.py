"""Состояния сигнала и переходы между ними (спека #95, раздел 3). Чистые функции, без БД.

Состояния: `new` → `in_work` → `resolved`; «Желательно» можно отложить (`snoozed`) до даты.
Отсрочка истекает сама: в день возврата сигнал снова `new`. Подъём уровня до «Обязательно»
снимает отсрочку. Решённый сигнал не меняется.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from clin_compass_knowledge.schemas.signal_decisions import SignalDecisions

from clin_compass.modules.alerts.schemas import AlertState

# Даты отсрочки — по московскому календарю. Перехода на летнее время с 2014 года нет.
MOSCOW = timezone(timedelta(hours=3))


ALREADY_RESOLVED = "Сигнал уже рассмотрен"
NO_DATA = "Вариантов решения нет: в knowledge/ нет signal-decisions.yaml"
ROUTING = "Сигнал маршрутизации решается направлением к психиатру"
MANDATORY_SNOOZE = "«Обязательно» отложить нельзя"
NO_CONTACT_KIND = "«Не удалось связаться» — только у сигналов о суицидальном риске"


def moscow_today() -> date:
    return datetime.now(MOSCOW).date()


class ConflictError(Exception):
    """Действие невозможно в текущем состоянии сигнала (HTTP 409). Текст видит врач."""


class InvalidError(Exception):
    """Неверные данные действия (HTTP 422). Текст видит врач."""


@dataclass(frozen=True)
class Option:
    """Вариант решения: id и текст из `signal-decisions.yaml`, подсказка к причине."""

    id: str
    text: str
    reason_hint: str


def effective_state(state: str, snoozed_until: date | None, today: date) -> AlertState:
    """Состояние для врача: отсрочка, дата которой наступила, — снова `new`."""
    if state == "snoozed" and (snoozed_until is None or snoozed_until <= today):
        return "new"
    return state  # type: ignore[return-value]


def after_acknowledge(state: str) -> AlertState | None:
    """«Взять в работу»: новое состояние или None, если ничего не меняется.

    Отложенный сигнал берётся в работу и до даты возврата: отсрочка снимается.
    """
    if state in ("new", "snoozed"):
        return "in_work"
    return None


def check_snooze(
    level: str, state: str, until: date, today: date, decisions: SignalDecisions | None
) -> None:
    """Отсрочка «Желательно» с завтра и не дальше `max_days` от сегодня.

    Raises:
        ConflictError: «Обязательно», сигнал решён или нет данных об отсрочке.
        InvalidError: Дата вне пределов.
    """
    if state == "resolved":
        raise ConflictError(ALREADY_RESOLVED)
    if level == "mandatory":
        raise ConflictError(MANDATORY_SNOOZE)
    if decisions is None:
        raise ConflictError(NO_DATA)
    last = today + timedelta(days=decisions.snooze.max_days)
    if not today < until <= last:
        raise InvalidError(f"Отложить можно с завтра и не дальше {last:%d.%m.%Y}")


def after_no_contact(kind: str, state: str) -> AlertState:
    """«Не удалось связаться»: сигнал остаётся или становится «в работе».

    Raises:
        ConflictError: Сигнал не о суицидальном риске или уже решён.
    """
    if kind != "alert":
        raise ConflictError(NO_CONTACT_KIND)
    if state == "resolved":
        raise ConflictError(ALREADY_RESOLVED)
    return "in_work"


def after_raise(state: str) -> AlertState | None:
    """Уровень поднялся до «Обязательно»: отложенный сигнал снова новый."""
    return "new" if state == "snoozed" else None


def decision_options(
    kind: str, safety_kind: str | None, decisions: SignalDecisions | None
) -> list[Option]:
    """Варианты решения по виду сигнала.

    `alert` (правила шкал и AI) — раздел `safety`; `safety` (терапия) — раздел `therapy`,
    отбор по `safety_kind`; без него — все варианты. Маршрутизацию решает направление (#35).

    Raises:
        ConflictError: Сигнал маршрутизации или нет данных.
    """
    if kind == "routing":
        raise ConflictError(ROUTING)
    if decisions is None:
        raise ConflictError(NO_DATA)
    if kind == "safety":
        return [
            Option(d.id, d.text, d.reason_hint)
            for d in decisions.therapy.decisions
            if safety_kind is None or safety_kind in d.applies_to
        ]
    return [Option(d.id, d.text, d.reason_hint) for d in decisions.safety.decisions]


def check_resolve(
    level: str, state: str, options: list[Option], decision_id: str, reason: str | None
) -> Option:
    """Проверить решение: вариант из списка, у «Обязательно» — непустая причина.

    Raises:
        ConflictError: Сигнал уже решён.
        InvalidError: Варианта нет в списке или нет причины.
    """
    if state == "resolved":
        raise ConflictError(ALREADY_RESOLVED)
    chosen = next((o for o in options if o.id == decision_id), None)
    if chosen is None:
        raise InvalidError(f"Нет варианта решения {decision_id}")
    if level == "mandatory" and not (reason or "").strip():
        raise InvalidError("Для «Обязательно» причина решения обязательна")
    return chosen
