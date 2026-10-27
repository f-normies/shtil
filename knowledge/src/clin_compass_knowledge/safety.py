"""Исполнение условий правил безопасности препарата (спека #29, разделы 3–4).

Три исхода: `yes` — условие выполнено, `no` — нет, `unknown` — данных не хватает.
Неизвестное значение не считается отсутствием противопоказания: врач видит, что уточнить.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta

from clin_compass_knowledge.schemas.drug import (
    CASE_TODAY,
    CaseGiven,
    FactCondition,
    MedicationCondition,
    Outcome,
    SafetyCondition,
    SafetyWhen,
)
from clin_compass_knowledge.schemas.drug_group import DrugGroup

_FACT_TITLES = {
    "egfr": "СКФ",
    "child_pugh": "класс по Чайлд-Пью",
    "pregnant": "беременность",
    "breastfeeding": "грудное вскармливание",
}
_FEMALE_ONLY = {"pregnant", "breastfeeding"}
# Части комбинированного МНН в снимке ГРЛС: «А+Б», «А+[Б+В]; Г», «А+Б и В+Г» (день и ночь).
_PARTS = re.compile(r"\+|;| и ")


@dataclass(frozen=True)
class Medication:
    """Препарат пациента. `inn` — МНН, если удалось сопоставить."""

    name: str
    inn: str | None
    started_on: date | None
    stopped_on: date | None


@dataclass(frozen=True)
class Facts:
    today: date
    sex: str | None = None
    diagnoses: frozenset[str] = frozenset()
    values: Mapping[str, object] = field(default_factory=dict)
    medications: tuple[Medication, ...] = ()


@dataclass(frozen=True)
class Result:
    """Исход условия правила, причины для врача и первый разрешённый день, если он есть."""

    outcome: Outcome
    reasons: tuple[str, ...] = ()
    until: date | None = None


def evaluate(when: SafetyWhen, facts: Facts, groups: Mapping[str, DrugGroup]) -> Result:
    """`yes`, если выполнено хоть одно условие; иначе `unknown`, если хоть одно неизвестно."""
    results = [_condition(c, facts, groups) for c in when.any]
    outcomes: tuple[Outcome, ...] = ("yes", "unknown")
    for outcome in outcomes:
        matched = [r for r in results if r.outcome == outcome]
        if matched:
            untils = [r.until for r in matched if r.until is not None]
            return Result(
                outcome,
                tuple(reason for r in matched for reason in r.reasons),
                max(untils) if untils else None,
            )
    return Result("no")


def case_facts(given: CaseGiven) -> Facts:
    """Данные пациента табличного случая на дату `CASE_TODAY`."""
    return Facts(
        today=CASE_TODAY,
        sex=given.sex,
        diagnoses=frozenset(given.diagnoses),
        values={str(name): value for name, value in given.facts.items()},
        medications=tuple(
            Medication(
                name=m.inn,
                inn=m.inn,
                started_on=None,
                stopped_on=None
                if m.stopped_days_ago is None
                else CASE_TODAY - timedelta(days=m.stopped_days_ago),
            )
            for m in given.medications
        ),
    )


def _condition(condition: SafetyCondition, facts: Facts, groups: Mapping[str, DrugGroup]) -> Result:
    if condition.diagnosis is not None:
        found = sorted(d for d in facts.diagnoses if icd10_matches(d, condition.diagnosis))
        return Result("yes", tuple(f"диагноз {d}" for d in found)) if found else Result("no")
    if condition.fact is not None:
        return _fact(condition.fact, facts)
    if condition.medication is not None:
        return _medication(condition.medication, facts, groups)
    return Result("unknown", (f"уточните: {condition.ask}",))


def icd10_matches(code: str, patterns: tuple[str, ...]) -> bool:
    """Код пациента входит в рубрику (`G40` → `G40.1`), подрубрику (`K70.3` → `K70.30`)
    или диапазон (`F00-F09`, `K74.3-K74.6`). По ней же `therapy` выбирает КР по `icd10`
    источника (спека #107).

    С шаблоном сравнивается начало кода той же длины: `K74.69` входит в `K74.3-K74.6`,
    `K70.30` — в `K70.3`, а рубрика `K74` без подрубрики не входит ни в одну подрубрику.
    """
    for pattern in patterns:
        low, _, high = pattern.partition("-")
        if low <= code[: len(low)] <= (high or low):
            return True
    return False


def _fact(condition: FactCondition, facts: Facts) -> Result:
    title = _FACT_TITLES[condition.name]
    if condition.name in _FEMALE_ONLY and facts.sex == "male":
        return Result("no")
    value = facts.values.get(condition.name)
    if value is None:
        return Result(
            "unknown",
            (f"{title} не указана" if condition.name == "egfr" else f"{title}: не указано",),
        )
    if condition.in_ is not None:
        hit = str(value) in condition.in_
    else:
        hit = _compare(value, condition.op, condition.value)  # type: ignore[arg-type]
    return Result("yes", (f"{title}: {_shown(value)}",)) if hit else Result("no")


def _compare(value: object, op: str, expected: object) -> bool:
    if op == "==":
        return value == expected
    left, right = float(value), float(expected)  # type: ignore[arg-type]
    return {"<": left < right, "<=": left <= right, ">": left > right, ">=": left >= right}[op]


def _shown(value: object) -> str:
    if isinstance(value, bool):
        return "да" if value else "нет"
    return str(value)


def _medication(
    condition: MedicationCondition, facts: Facts, groups: Mapping[str, DrugGroup]
) -> Result:
    members = {_norm(inn) for inn in groups[condition.group].inn}
    reasons: list[str] = []
    untils: list[date] = []
    for m in facts.medications:
        found = [c for c in components(m.inn) if _member(c, members)]
        if not found:
            continue
        label = f"{m.name} ({', '.join(found)})"
        if m.stopped_on is None or m.stopped_on > facts.today:
            reasons.append(f"{label}: принимает")
        elif condition.within_days is not None:
            # «В течение 2 недель после отмены»: 14-й день ещё входит в срок (ревью медика #76).
            until = m.stopped_on + timedelta(days=condition.within_days + 1)
            if facts.today < until:
                reasons.append(f"{label}: отменён {m.stopped_on:%d.%m.%Y}")
                untils.append(until)
    if not reasons:
        return Result("no")
    # Пока препарат принимают, дата окончания срока не определена.
    taking = len(untils) < len(reasons)
    return Result("yes", tuple(reasons), None if taking or not untils else max(untils))


def components(inn: str | None) -> list[str]:
    """Действующие вещества МНН без повторов: части через «+», «;» и « и », без «[ ]»."""
    text = _norm(inn or "").replace("[", " ").replace("]", " ")
    parts = (" ".join(part.split()) for part in _PARTS.split(text))
    return list(dict.fromkeys(part for part in parts if part))


def _member(component: str, members: set[str]) -> bool:
    """Компонент — член группы или член группы с уточнением целым словом: «фенобарбитал натрия»."""
    return component in members or any(component.startswith(f"{m} ") for m in members)


def _norm(name: str) -> str:
    return name.strip().lower().replace("ё", "е")
