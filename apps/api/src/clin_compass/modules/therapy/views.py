"""Ответы и снимки модуля therapy: вариант подбора, снимок справочника, назначение.

Чистые функции без БД: из данных справочника и строки назначения собирают то, что видит
врач и что хранится в предложении.
"""

import hashlib
from datetime import date, timedelta, timezone
from typing import Any

from clin_compass_knowledge.schemas.common import SourceRef
from clin_compass_knowledge.schemas.drug import Drug, Form, Indication

from clin_compass.modules.knowledge.schemas import RegistryInfo
from clin_compass.modules.proposals.schemas import ProposalOut
from clin_compass.modules.therapy import checks
from clin_compass.modules.therapy.checks import Catalog, Regimen
from clin_compass.modules.therapy.models import Prescription, PrescriptionAppearance
from clin_compass.modules.therapy.schemas import (
    AppearanceOrigin,
    AppearanceState,
    CheckOut,
    InstructionOut,
    LabelRegimen,
    OptionOut,
    PillAppearance,
    PrescriptionOut,
    PrescriptionStatus,
    ReactionOption,
    SourcedPillAppearance,
    SourceLink,
)

# Даты приёма — по московскому календарю. Перехода на летнее время с 2014 года нет.
MOSCOW = timezone(timedelta(hours=3))


def payload(
    catalog: Catalog, drug: Drug, indication: Indication, run: list[checks.Check]
) -> dict[str, Any]:
    """Снимок справочника для предложения и будущего назначения."""
    built = option(catalog, drug, indication, run, None)
    return {
        "drug_id": drug.id,
        "trade_name": drug.trade_name,
        "inn": drug.inn,
        "indication_id": indication.id,
        "indication_title": indication.title,
        "indication_icd10": built.indication_icd10,
        "regimen": built.regimen.model_dump(mode="json"),
        "instructions": [i.model_dump(mode="json") for i in built.instructions],
        "checks": [c.model_dump(mode="json") for c in built.checks],
        "registration": built.registration.model_dump(mode="json") if built.registration else None,
        "off_label": False,
        "off_label_reason": None,
        "card_hash": card_hash(drug),
    }


def card_hash(drug: Drug) -> str:
    """Хеш карточки препарата: по нему подтверждение узнаёт, что карточка изменилась."""
    return hashlib.sha256(drug.model_dump_json().encode()).hexdigest()


def option(
    catalog: Catalog,
    drug: Drug,
    indication: Indication,
    run: list[checks.Check],
    proposal: ProposalOut | None,
) -> OptionOut:
    r = indication.regimen
    form = next(f for f in drug.forms if f.id == r.form)
    return OptionOut(
        drug_id=drug.id,
        trade_name=drug.trade_name,
        inn=drug.inn,
        indication_id=indication.id,
        indication_title=indication.title,
        indication_icd10=list(indication.icd10),
        regimen=LabelRegimen(
            form=form.title,
            strength_mg=form.strength_mg,
            dose_mg=r.dose_mg,
            times_per_day=r.times_per_day,
            schedule=r.schedule,
            duration_days=r.duration.days,
            max_days=r.duration.max_days,
            duration_text=r.duration.text,
            source=link(catalog, r.source),
        ),
        instructions=[
            InstructionOut(text=i.text, source=link(catalog, i.source)) for i in drug.instructions
        ],
        checks=[
            CheckOut(
                id=c.id,
                title=c.title,
                status=c.status,
                detail=c.detail,
                source=link(catalog, c.source) if c.source else None,
                actions=list(c.actions),
            )
            for c in run
        ],
        registration=registration(catalog, drug),
        proposal=proposal,
    )


def registration(catalog: Catalog, drug: Drug) -> RegistryInfo | None:
    """Номер РУ из карточки, статус и дата снимка ГРЛС (спека #28, 8.7)."""
    ref = drug.registration.source
    registry = catalog.registries.get(ref.source)
    entry = registry.by_number(drug.registration.number) if registry is not None else None
    if entry is None:
        return None
    return RegistryInfo(
        reg_number=drug.registration.number,
        status=entry.status,
        snapshot_date=catalog.sources[ref.source].date,
    )


def link(catalog: Catalog, ref: SourceRef) -> SourceLink:
    source = catalog.sources[ref.source]
    return SourceLink(
        source=ref.source,
        locator=ref.locator,
        title=source.title,
        url=str(source.url),
        version=source.version,
        date=source.date,
    )


def reaction_options(drug: Drug) -> list[ReactionOption]:
    """Реакции, которые пациент может отметить, его словами. Лабораторные показатели — нет.

    Без действий врача (`reaction_actions`) отметку не принять, поэтому и не предлагаем.
    """
    if drug.reaction_actions is None:
        return []
    return [
        ReactionOption(id=r.id, title=r.patient_title)
        for r in drug.adverse_reactions
        if r.patient_reportable and r.patient_title is not None
    ]


def appearance_state(
    catalog: Catalog, row: Prescription, override: PrescriptionAppearance | None
) -> AppearanceState:
    """Вид таблетки по единственной совпавшей форме снимка или личной поправке пациента."""
    form = _form_for_snapshot(catalog, row)
    default = None
    effective = None
    origin: AppearanceOrigin = "unknown"
    if form is not None and form.appearance is not None:
        source = link(catalog, form.appearance.source)
        default = SourcedPillAppearance(
            shape=form.appearance.shape,
            color=form.appearance.color,
            description=form.appearance.description,
            source=source,
        )
        effective = PillAppearance(shape=form.appearance.shape, color=form.appearance.color)
        origin = "instruction"
    if override is not None and override.shape is not None:
        effective = PillAppearance(shape=override.shape, color=override.color)
        origin = "patient"
    return AppearanceState(
        default_appearance=default,
        appearance=effective,
        appearance_origin=origin,
        appearance_revision=override.revision if override is not None else 0,
    )


def _form_for_snapshot(catalog: Catalog, row: Prescription) -> Form | None:
    regimen = row.card_snapshot.get("regimen")
    if not isinstance(regimen, dict):
        return None
    drug = catalog.drugs.get(row.drug_id)
    if drug is None:
        return None
    stable_id = regimen.get("form_id")
    if isinstance(stable_id, str):
        matches = [form for form in drug.forms if form.id == stable_id]
        if len(matches) == 1:
            return matches[0]
        if matches:
            return None
    form_value = regimen.get("form")
    if not isinstance(form_value, str):
        return None
    title_matches = [form for form in drug.forms if form.title == form_value]
    if len(title_matches) == 1:
        return title_matches[0]
    if title_matches:
        return None
    id_matches = [form for form in drug.forms if form.id == form_value]
    return id_matches[0] if len(id_matches) == 1 else None


def regimen_fields(regimen: Regimen) -> dict[str, Any]:
    return {
        "dose_mg": regimen.dose_mg,
        "times_per_day": regimen.times_per_day,
        "duration_days": regimen.duration_days,
    }


def indication_of(drug: Drug, indication_id: str) -> Indication | None:
    return next((i for i in drug.indications if i.id == indication_id), None)


def ends_on(row: Prescription) -> date | None:
    if row.duration_days is None:
        return None
    return row.started_at.astimezone(MOSCOW).date() + timedelta(days=row.duration_days)


def status_of(row: Prescription, today: date) -> PrescriptionStatus:
    if row.stopped_at is not None:
        return "stopped"
    last = ends_on(row)
    return "completed" if last is not None and today >= last else "active"


def prescription_out(row: Prescription, today: date) -> PrescriptionOut:
    snapshot = row.card_snapshot
    return PrescriptionOut(
        id=row.id,
        patient_id=row.patient_id,
        drug_id=row.drug_id,
        indication_id=row.indication_id,
        trade_name=snapshot["trade_name"],
        inn=snapshot["inn"],
        dose_mg=row.dose_mg,
        times_per_day=row.times_per_day,
        schedule=row.schedule,
        duration_days=row.duration_days,
        ends_on=ends_on(row),
        status=status_of(row, today),
        off_label=row.off_label,
        off_label_reason=row.off_label_reason,
        card_snapshot=snapshot,
        proposal_id=row.proposal_id,
        confirmed_by=row.confirmed_by,
        started_at=row.started_at,
        stopped_at=row.stopped_at,
        stopped_by=row.stopped_by,
        stop_reason=row.stop_reason,
    )
