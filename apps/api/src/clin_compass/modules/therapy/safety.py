"""Безопасность терапии (спека #29): препараты пациента и повторная проверка назначений.

Препараты вне системы вносит врач, МНН берётся из снимка ГРЛС. После любого изменения данных
пациента правила безопасности проверяются по действующим назначениям; правило, чей статус
стал хуже, поднимает сигнал врачу. Сама система назначения не меняет.
"""

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from uuid import UUID

from clin_compass_knowledge.safety import Medication as FactMedication
from clin_compass_knowledge.schemas.common import SourceRef
from clin_compass_knowledge.schemas.drug import Drug
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from clin_compass.modules.alerts import service as alerts
from clin_compass.modules.alerts.schemas import SourceSnapshot
from clin_compass.modules.patients import service as patients
from clin_compass.modules.patients.schemas import PatientSummary
from clin_compass.modules.therapy import checks, views
from clin_compass.modules.therapy.checks import Catalog, Profile
from clin_compass.modules.therapy.models import Medication, Prescription
from clin_compass.modules.therapy.schemas import (
    DrugLookupOut,
    DrugMatchOut,
    MedicationIn,
    MedicationOut,
)

# Чем хуже статус правила, тем больше ранг. `manual` не хуже `pass`: врач уже видел вопрос.
_RANK = {"pass": 0, "manual": 0, "warn": 1, "fail": 2}
_LEVEL = {"warn": "desirable", "fail": "mandatory"}
_SAFETY_PREFIXES = ("contra:", "caution:")
# Препарат без МНН: его взаимодействия не проверены. Новый такой препарат — сигнал «желательно».
_UNMATCHED = "unmatched:"
# `rule_id` сигнала — не длиннее колонки `alerts.rule_id`.
_RULE_ID_MAX = 200


@dataclass(frozen=True)
class SafetySignal:
    """Правило, статус которого стал хуже. `rule_id` — id правила карточки или `unmatched:…`."""

    rule_id: str
    level: str
    check: checks.Check


class MedicationNotFoundError(Exception):
    """Препарата нет у этого пациента."""


class MedicationDatesError(Exception):
    """Дата отмены раньше даты начала приёма."""


def lookup(catalog: Catalog, name: str) -> DrugLookupOut:
    """Сверка названия со снимком ГРЛС: статус и найденные записи с МНН."""
    if catalog.registry is None:
        return DrugLookupOut(check="not_found", matches=[])
    found = catalog.registry.lookup(name)
    return DrugLookupOut(
        check=found.check,
        matches=[
            DrugMatchOut(trade_name=m.trade_name, inn=m.inn, reg_number=m.reg_number)
            for m in found.matches
        ],
        inn=found.inn,
        inn_options=list(found.inn_options),
    )


async def list_medications(session: AsyncSession, patient_id: UUID) -> list[MedicationOut]:
    rows = await session.scalars(
        select(Medication)
        .where(Medication.patient_id == patient_id)
        .order_by(Medication.created_at)
    )
    return [MedicationOut.model_validate(m) for m in rows]


async def add_medication(
    session: AsyncSession,
    catalog: Catalog,
    patient_id: UUID,
    body: MedicationIn,
    *,
    doctor_id: UUID,
) -> MedicationOut:
    """МНН — выбранное врачом или из точного совпадения в ГРЛС с любым статусом регистрации.

    Для взаимодействий статус регистрации не важен: моклобемид в снимке снят с регистрации.
    Удостоверений у названия обычно несколько: МНН берётся, если он у всех один.
    """
    found = lookup(catalog, body.name)
    row = Medication(
        patient_id=patient_id,
        name=body.name,
        inn=body.inn or found.inn,
        registry_check=found.check,
        started_on=body.started_on,
        stopped_on=body.stopped_on,
        entered_by=doctor_id,
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)
    return MedicationOut.model_validate(row)


async def stop_medication(
    session: AsyncSession, patient_id: UUID, medication_id: UUID, stopped_on: date
) -> MedicationOut:
    row = await session.get(Medication, medication_id, with_for_update=True)
    if row is None or row.patient_id != patient_id:
        raise MedicationNotFoundError
    if row.started_on is not None and stopped_on < row.started_on:
        raise MedicationDatesError
    row.stopped_on = stopped_on
    await session.flush()
    return MedicationOut.model_validate(row)


async def load_profile(session: AsyncSession, patient: PatientSummary, today: date) -> Profile:
    """Всё, по чему проверяется препарат: диагнозы, факты профиля, препараты и назначения."""
    diagnoses = await patients.list_diagnoses(session, patient.id)
    rows = await session.scalars(
        select(Prescription).where(
            Prescription.patient_id == patient.id, Prescription.stopped_at.is_(None)
        )
    )
    prescribed = {
        r.drug_id: r.started_at.astimezone(views.MOSCOW).date()
        for r in rows
        if views.status_of(r, today) == "active"
    }
    values = {
        "egfr": patient.egfr,
        "child_pugh": patient.child_pugh,
        "pregnant": patient.pregnant,
        "breastfeeding": patient.breastfeeding,
    }
    return Profile(
        birth_date=patient.birth_date,
        diagnoses=frozenset(d.icd10 for d in diagnoses if d.resolved_at is None),
        prescribed_since=prescribed,
        sex=patient.sex,
        values={k: v for k, v in values.items() if v is not None},
        medications=tuple(
            FactMedication(name=m.name, inn=m.inn, started_on=m.started_on, stopped_on=m.stopped_on)
            for m in await list_medications(session, patient.id)
        ),
    )


def initial_state(payload_checks: list[dict[str, str]]) -> dict[str, str]:
    """Состояние правил безопасности на момент назначения — из проверок предложения."""
    tracked = (*_SAFETY_PREFIXES, _UNMATCHED)
    return {c["id"]: c["status"] for c in payload_checks if c["id"].startswith(tracked)}


async def recheck(
    session: AsyncSession, catalog: Catalog, *, patient: PatientSummary, today: date
) -> None:
    """Проверить действующие назначения по новым данным; ухудшение — сигнал врачу."""
    profile = await load_profile(session, patient, today)
    rows = (
        await session.scalars(
            select(Prescription)
            .where(Prescription.patient_id == patient.id, Prescription.stopped_at.is_(None))
            .with_for_update()
        )
    ).all()
    for row in rows:
        drug = catalog.drugs.get(row.drug_id)
        if drug is None or views.status_of(row, today) != "active":
            continue
        found = checks.safety_checks(drug, profile, catalog, today)
        row.safety_state, signals = worsened(row.safety_state, found)
        for signal in signals:
            await _signal(session, catalog, drug, row, signal)
    await session.flush()


def worsened(
    state: Mapping[str, str], found: list[checks.Check]
) -> tuple[dict[str, str], list[SafetySignal]]:
    """Новое состояние правил назначения и сигналы по правилам, чей статус стал хуже.

    Хуже — `pass`/`manual` → `warn` или `fail`, `warn` → `fail`. Правила, которых нет в `state`,
    считаются `pass`: у назначения до #29 проверок безопасности не было, врач их не видел.
    Препарат без МНН, которого не было в `state`, — сигнал «желательно».
    """
    new_state = dict(state)
    signals: list[SafetySignal] = []
    for check in found:
        before = state.get(check.id)
        new_state[check.id] = check.status
        if check.id.startswith(_UNMATCHED):
            if before is None:
                signals.append(SafetySignal(check.id, "desirable", check))
        elif check.status in _LEVEL and _RANK[check.status] > _RANK[before or "pass"]:
            signals.append(SafetySignal(check.id.split(":", 1)[1], _LEVEL[check.status], check))
    return new_state, signals


async def _signal(
    session: AsyncSession, catalog: Catalog, drug: Drug, row: Prescription, signal: SafetySignal
) -> None:
    """Сигнал врачу. Препарат без МНН — свой `rule_id` на каждое название: иначе второй
    такой препарат дописался бы к открытому сигналу первого и врач его не увидел."""
    check = signal.check
    refs = _rule_sources(drug, check.id) or ((check.source,) if check.source else ())
    await alerts.raise_safety(
        session,
        patient_id=row.patient_id,
        rule_id=signal_rule_id(drug.id, signal.rule_id),
        rule_version=drug.version,
        level=signal.level,
        title=f"{drug.trade_name}: {check.title}",
        doctor_actions=list(check.actions),
        sources=[SourceSnapshot(**views.link(catalog, ref).model_dump()) for ref in refs],
        evidence={
            "prescription_id": str(row.id),
            "detail": check.detail,
            "safety_kind": safety_kind(check.id),
        },
        origin_ref=str(row.id),
    )


def safety_kind(check_id: str) -> str:
    """Вид сигнала для вариантов решения (спека #95): противопоказание или предосторожность.

    Препарат без МНН — как мера предосторожности: его взаимодействия не проверены.
    """
    return "contraindication" if check_id.startswith("contra:") else "caution"


def signal_rule_id(drug_id: str, rule_id: str) -> str:
    """`{препарат}/{правило}` не длиннее колонки `alerts.rule_id`.

    Длинное название препарата без МНН обрезается, хвост заменяет короткий хеш полного id:
    два названия с одинаковым началом не сливаются в один сигнал.
    """
    full = f"{drug_id}/{rule_id}"
    if len(full) <= _RULE_ID_MAX:
        return full
    digest = hashlib.sha256(full.encode()).hexdigest()[:12]
    return f"{full[: _RULE_ID_MAX - len(digest) - 1]}~{digest}"


def _rule_sources(drug: Drug, check_id: str) -> tuple[SourceRef, ...]:
    """Все источники правила карточки по id проверки (`contra:maoi`, `caution:sedative`)."""
    section, _, rule_id = check_id.partition(":")
    rules = {"contra": drug.contraindications, "caution": drug.cautions}.get(section, ())
    return next((r.sources for r in rules if r.id == rule_id), ())
