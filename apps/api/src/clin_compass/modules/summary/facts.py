"""Факты сводки из данных периода (спека #38, 3.2). Модель видит только их.

Факт — результат шкалы, сигнал врачу или подтверждённая находка дневника и чата: вид, дата,
текст и раздел карточки. Номера идут по дате. Тексты собирает код, поэтому каждое число
утверждения модели можно сверить с текстом фактов (`drafting.verify`). Даты — по местному
времени.

В период попадает то, что появилось с его начала: результат шкалы — по времени поступления
на сервер (заполненное офлайн врач ещё не видел), сигнал — по созданию, срабатыванию или
действию врача, находка — по подтверждению врачом.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from itertools import groupby
from uuid import UUID
from zoneinfo import ZoneInfo

from clin_compass.modules.alerts.schemas import AlertLevel, AlertOut
from clin_compass.modules.assessments.schemas import AssessmentOut
from clin_compass.modules.freetext.schemas import FindingOut
from clin_compass.modules.summary.schemas import FactKind, FactSection, SummaryFact

_TOTAL = "total"
_ABBREVIATION = re.compile(r"\(([^()]+)\)\s*$")
_KIND_ORDER: dict[FactKind, int] = {"scale": 0, "signal": 1, "finding": 2}
_LEVEL = {"mandatory": "обязательно", "desirable": "желательно"}
_SIGNAL = {
    "alert": "Сигнал",
    "routing": "Сигнал о направлении к психиатру",
    "safety": "Предупреждение о безопасности",
}
_STATE = {"new": "новый", "in_work": "в работе", "resolved": "закрыт"}
_SOURCE: dict[str, tuple[str, FactSection]] = {
    "diary": ("Дневник", "diary"),
    "message": ("Чат", "messages"),
}
_CATEGORY = {
    "symptom": "симптом",
    "side_effect": "нежелательное явление",
    "adherence": "приём препарата",
    "medication": "другой препарат",
}
_EVENT = {
    "taken": "приём",
    "missed": "пропуск",
    "stopped": "прекращение приёма",
    "dose_changed": "самостоятельное изменение дозы",
}


@dataclass(frozen=True)
class _Draft:
    at: datetime
    kind: FactKind
    ref: str
    text: str
    section: FactSection
    level: AlertLevel | None = None


def collect(
    *,
    start: datetime,
    results: Sequence[AssessmentOut],
    alerts: Sequence[AlertOut],
    findings: Sequence[FindingOut],
    written: Mapping[UUID, datetime],
    zone: ZoneInfo,
) -> list[SummaryFact]:
    """Факты периода с `start`, пронумерованные по дате.

    Args:
        start: Начало периода.
        results: Все результаты шкал пациента: прошлый результат нужен для изменения.
        alerts: Сигналы пациента с историей.
        findings: Подтверждённые находки дневника и чата.
        written: Когда пациент написал текст находки, по id разбора; нет — дата подтверждения.
        zone: Часовой пояс дат в тексте.
    """
    drafts = [
        *_scale_facts(start, results, zone),
        *(d for a in alerts if (d := _signal_fact(start, a, zone)) is not None),
        *(_finding_fact(f, written) for f in findings if f.confirmed_at >= start),
    ]
    drafts.sort(key=lambda d: (d.at, _KIND_ORDER[d.kind], d.ref))
    return [
        SummaryFact(number=n, kind=d.kind, at=d.at, text=d.text, section=d.section, level=d.level)
        for n, d in enumerate(drafts, start=1)
    ]


def _scale_facts(start: datetime, results: Sequence[AssessmentOut], zone: ZoneInfo) -> list[_Draft]:
    """Результаты, поступившие в период; изменение — к прошлому результату той же шкалы."""
    drafts: list[_Draft] = []
    ordered = sorted(results, key=lambda r: (r.scale_id, r.completed_at, str(r.id)))
    for _, group in groupby(ordered, key=lambda r: r.scale_id):
        previous: AssessmentOut | None = None
        for current in group:
            if current.received_at >= start:
                drafts.extend(_scale_drafts(current, previous, zone))
            previous = current
    return drafts


def _scale_drafts(
    current: AssessmentOut, previous: AssessmentOut | None, zone: ZoneInfo
) -> list[_Draft]:
    name = _abbreviation(current.scale_title)
    drafts = []
    for key in sorted(current.scores):
        score = current.scores[key]
        label = name if key == _TOTAL else f"{name}, {key}"
        points = _points(score.value, score.band.label)
        text = f"{label} {_day(current.completed_at, zone)}: {points}."
        before = previous.scores.get(key) if previous is not None else None
        if previous is None or before is None:
            text += " Первое заполнение шкалы."
        else:
            was = _points(before.value, before.band.label)
            change = _change(score.value - before.value)
            text += f" К прошлому результату {_day(previous.completed_at, zone)} ({was}): {change}."
        drafts.append(_Draft(current.completed_at, "scale", str(current.id), text, "assessments"))
    return drafts


def _signal_fact(start: datetime, alert: AlertOut, zone: ZoneInfo) -> _Draft | None:
    """Сигнал со срабатыванием или действием в периоде; дата — первое событие периода."""
    actions = [action for action in alert.actions if action.at >= start]
    moments = (
        alert.created_at,
        alert.last_triggered_at,
        alert.resolved_at,
        *(a.at for a in actions),
    )
    at = min((m for m in moments if m is not None and m >= start), default=None)
    if at is None:
        return None
    text = f"{_SIGNAL[alert.kind]} «{_LEVEL[alert.level]}»: {alert.title}. {_state(alert, zone)}"
    for action in actions:
        if action.action == "no_contact":
            text += f" Не удалось связаться {_day(action.at, zone)}."
    if alert.occurrences > 1:
        text += f" Срабатываний: {alert.occurrences}."
    return _Draft(at, "signal", str(alert.id), text, "signals", alert.level)


def _state(alert: AlertOut, zone: ZoneInfo) -> str:
    decision = alert.decision
    if alert.state == "resolved" and decision is not None and decision.decision_text:
        return f"Решение врача: {decision.decision_text}."
    if alert.state == "snoozed" and alert.snoozed_until is not None:
        return f"Отложен до {_date(alert.snoozed_until)}."
    return f"Состояние: {_STATE.get(alert.state, 'отложен')}."


def _finding_fact(finding: FindingOut, written: Mapping[UUID, datetime]) -> _Draft:
    data = finding.data
    source, section = _SOURCE[data.get("source_type") or "diary"]
    record = f"{source}, голосовое" if data.get("transcript") else source
    details = [
        name
        for name in (
            (data.get("drug") or {}).get("name"),
            _EVENT.get(data.get("adherence_event") or ""),
        )
        if name
    ]
    category = _CATEGORY[finding.category]
    if details:
        category += f" ({', '.join(details)})"
    text = f"{record}, {category}: {data.get('summary', '')}. Цитата: «{data.get('quote', '')}»"
    at = written.get(finding.analysis_id, finding.confirmed_at)
    return _Draft(at, "finding", str(finding.id), text, section)


def _abbreviation(title: str) -> str:
    """«Опросник здоровья пациента (PHQ-9)» → «PHQ-9»; без скобок в конце — всё название."""
    match = _ABBREVIATION.search(title)
    return match.group(1) if match is not None else title


def _points(value: int, band: str) -> str:
    label = band[:1].lower() + band[1:]
    return f"{value} {_plural(value)}, {label}" if label else f"{value} {_plural(value)}"


def _plural(value: int) -> str:
    """«1 балл», «2 балла», «5 баллов», «11 баллов», «21 балл»."""
    tens, ones = abs(value) % 100, abs(value) % 10
    if ones == 1 and tens != 11:
        return "балл"
    if 2 <= ones <= 4 and not 12 <= tens <= 14:
        return "балла"
    return "баллов"


def _change(delta: int) -> str:
    if delta == 0:
        return "без изменений"
    return f"+{delta}" if delta > 0 else f"−{-delta}"


def _day(moment: datetime, zone: ZoneInfo) -> str:
    return _date(moment.astimezone(zone).date())


def _date(day: date) -> str:
    return f"{day.day:02d}.{day.month:02d}"
