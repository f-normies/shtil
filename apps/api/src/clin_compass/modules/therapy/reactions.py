"""Нежелательные реакции (спека #29, разделы 3 и 6): отметка пациентом и черновик извещения.

Пациент выбирает реакции из перечня инструкции своего действующего назначения. Каждая
отметка хранится и поднимает врачу сигнал безопасности с наибольшим уровнем среди отмеченных.
Черновик извещения собирается из назначения, отметок и препаратов пациента без LLM;
отправляет его врач.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from uuid import UUID

from clin_compass_knowledge.schemas.common import SourceRef
from clin_compass_knowledge.schemas.drug import AdverseReaction, Drug
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from clin_compass.modules.alerts import service as alerts
from clin_compass.modules.alerts.schemas import SourceSnapshot
from clin_compass.modules.identity import service as identity
from clin_compass.modules.patients import service as patients
from clin_compass.modules.therapy import safety, views
from clin_compass.modules.therapy.checks import Catalog, age_on, years_text
from clin_compass.modules.therapy.models import Prescription, ReactionReport
from clin_compass.modules.therapy.schemas import (
    AdverseEventDraftOut,
    MedicationOut,
    ReactionReportIn,
    ReactionReportOut,
    ReportedReaction,
)
from clin_compass.modules.therapy.service import TherapyConflictError, TherapyNotFoundError

NOT_ACTIVE = "Назначение уже не действует"
NO_REPORTS = "У назначения нет отметок о нежелательных реакциях"
NO_CARD = "Перечня реакций для этого препарата нет"
BLANK = "______ (заполняет врач)"
_SEX = {"male": "мужской пол", "female": "женский пол"}


class UnknownReactionError(Exception):
    """Реакции нет в перечне инструкции или её не отмечает пациент (лабораторный показатель)."""


@dataclass(frozen=True)
class Draft:
    """Всё, из чего собирается черновик извещения. Даты — по московскому календарю."""

    trade_name: str
    inn: str
    reg_number: str | None
    additional_monitoring: bool
    indication_title: str
    dose_mg: float
    times_per_day: int
    schedule: str
    started_on: date
    stopped_on: date | None
    stop_reason: str | None
    # День после последнего приёма курса с длительностью; курс без срока — None.
    course_end: date | None
    off_label_reason: str | None
    reports: Sequence[ReactionReportOut]
    medications: Sequence[MedicationOut]
    sex: str | None
    birth_date: date | None
    doctor_name: str
    today: date


def pick(drug: Drug, ids: Iterable[str]) -> list[AdverseReaction]:
    """Реакции карточки по id в порядке отметки, без повторов.

    Raises:
        UnknownReactionError: id нет среди реакций, которые отмечает пациент.
    """
    reportable = {r.id: r for r in drug.adverse_reactions if r.patient_reportable}
    chosen: dict[str, AdverseReaction] = {}
    for reaction_id in ids:
        if reaction_id not in reportable:
            raise UnknownReactionError(reaction_id)
        chosen.setdefault(reaction_id, reportable[reaction_id])
    return list(chosen.values())


def level_of(reactions: Sequence[AdverseReaction]) -> str:
    """Наибольший уровень среди отмеченных реакций."""
    return "mandatory" if any(r.level == "mandatory" for r in reactions) else "desirable"


def signal_title(trade_name: str, reactions: Sequence[AdverseReaction]) -> str:
    return f"Нежелательная реакция на «{trade_name}»: {_titles(r.title for r in reactions)}"


def signal_detail(
    reactions: Sequence[AdverseReaction], *, reported_on: date, started_on: date, note: str | None
) -> str:
    """Данные сигнала для врача: реакции, дата отметки, день приёма и текст пациента."""
    day = (reported_on - started_on).days + 1
    detail = (
        f"Отмечено {reported_on:%d.%m.%Y}, {day}-й день приёма (с {started_on:%d.%m.%Y}): "
        f"{_titles(r.title for r in reactions)}."
    )
    return f"{detail} Пациент пишет: «{note}»" if note else detail


def concomitant(medications: Iterable[MedicationOut], on: date) -> list[MedicationOut]:
    """Препараты, которые пациент принимал в этот день: начаты не позже, отменены не раньше."""
    return [
        m
        for m in medications
        if (m.started_on is None or m.started_on <= on)
        and (m.stopped_on is None or m.stopped_on >= on)
    ]


def draft_text(d: Draft) -> str:
    """Черновик извещения о нежелательной реакции. «Тяжесть» и «Исход» врач заполняет сам.

    Возраст — на день первой отметки: в извещении нужен возраст на момент реакции.
    """
    first = min(_moscow(r) for r in d.reports)
    patient = ", ".join(
        [
            _SEX.get(d.sex or "", "пол не указан"),
            years_text(age_on(d.birth_date, first)) if d.birth_date else "возраст не указан",
        ]
    )
    drug = f"{d.trade_name} ({d.inn})" + (f", РУ {d.reg_number}" if d.reg_number else "")
    lines = [
        "Извещение о нежелательной реакции — черновик",
        "Отправляет врач. Проверьте и дополните поля перед отправкой.",
        "",
        f"Пациент: {patient}",
        f"Подозреваемый препарат: {drug}",
    ]
    if d.additional_monitoring:
        lines.append("▼ Препарат подлежит дополнительному мониторингу")
    lines += [
        f"Показание: {d.indication_title}",
        f"Доза и режим: {d.dose_mg:g} мг {_times(d.times_per_day)} в сутки, {d.schedule}",
        f"Начало приёма: {d.started_on:%d.%m.%Y}",
        f"Отмена: {_stop_text(d)}",
    ]
    if d.off_label_reason:
        lines.append(f"Вне инструкции, обоснование врача: {d.off_label_reason}")
    lines += ["", "Нежелательные реакции:"]
    lines += [_report_line(r) for r in d.reports]
    taken = concomitant(d.medications, first)
    lines += ["", f"Сопутствующие препараты на {first:%d.%m.%Y}:"]
    lines += [_medication_line(m) for m in taken] or ["- нет данных о других препаратах"]
    lines += ["", f"Тяжесть: {BLANK}", f"Исход: {BLANK}", "", f"Врач: {d.doctor_name}"]
    return "\n".join(lines)


def _stop_text(d: Draft) -> str:
    if d.stopped_on:
        return f"{d.stopped_on:%d.%m.%Y}, причина: {d.stop_reason}"
    if d.course_end is not None and d.today >= d.course_end:
        return f"курс завершён, последний день приёма {d.course_end - timedelta(days=1):%d.%m.%Y}"
    return "препарат не отменён"


async def report(
    session: AsyncSession,
    catalog: Catalog,
    *,
    patient_id: UUID,
    prescription_id: UUID,
    body: ReactionReportIn,
    today: date,
) -> ReactionReportOut:
    """Сохранить отметку пациента и поднять врачу сигнал безопасности. Коммитит вызывающий.

    Raises:
        TherapyNotFoundError: Назначения нет или оно чужое.
        TherapyConflictError: Назначение не действует или карточки препарата нет.
        UnknownReactionError: Реакции нет в перечне для пациента.
    """
    row = await session.get(Prescription, prescription_id)
    if row is None:
        raise TherapyNotFoundError
    if row.patient_id != patient_id:
        raise TherapyNotFoundError(row.patient_id)
    if views.status_of(row, today) != "active":
        raise TherapyConflictError(NOT_ACTIVE)
    drug = catalog.drugs.get(row.drug_id)
    if drug is None or drug.reaction_actions is None:
        raise TherapyConflictError(NO_CARD)
    chosen = pick(drug, body.reactions)
    saved = ReactionReport(
        prescription_id=row.id,
        patient_id=patient_id,
        reactions=[_snapshot(r).model_dump() for r in chosen],
        note=body.note,
    )
    session.add(saved)
    await session.flush()
    await session.refresh(saved)
    refs = [*(ref for r in chosen for ref in r.sources), *drug.reaction_actions.sources]
    await alerts.raise_safety(
        session,
        patient_id=patient_id,
        # Свой сигнал на каждую отметку: заголовок открытого сигнала не меняется, и новые
        # реакции под старым заголовком врач бы не заметил.
        rule_id=safety.signal_rule_id(drug.id, f"reaction:{saved.id}"),
        rule_version=drug.version,
        level=level_of(chosen),
        title=signal_title(drug.trade_name, chosen),
        doctor_actions=list(drug.reaction_actions.doctor_actions),
        sources=[SourceSnapshot(**views.link(catalog, ref).model_dump()) for ref in _unique(refs)],
        evidence={
            "prescription_id": str(row.id),
            "report_id": str(saved.id),
            "safety_kind": "reaction",
            "detail": signal_detail(
                chosen,
                reported_on=saved.reported_at.astimezone(views.MOSCOW).date(),
                started_on=row.started_at.astimezone(views.MOSCOW).date(),
                note=saved.note,
            ),
        },
        origin_ref=str(saved.id),
    )
    return ReactionReportOut.model_validate(saved)


async def list_reports(session: AsyncSession, patient_id: UUID) -> list[ReactionReportOut]:
    """Все отметки пациента, старые первыми. Доступ проверяет вызывающий."""
    rows = await session.scalars(
        select(ReactionReport)
        .where(ReactionReport.patient_id == patient_id)
        .order_by(ReactionReport.reported_at)
    )
    return [ReactionReportOut.model_validate(r) for r in rows]


async def draft(
    session: AsyncSession, catalog: Catalog, *, doctor_id: UUID, prescription_id: UUID, today: date
) -> AdverseEventDraftOut:
    """Черновик извещения по назначению с отметками реакций, действующему или отменённому.

    Raises:
        TherapyNotFoundError: Назначения нет или врач не ведущий у пациента.
        TherapyConflictError: У назначения нет отметок.
    """
    row = await session.get(Prescription, prescription_id)
    if row is None:
        raise TherapyNotFoundError
    patient = await patients.get_patient_for_doctor(
        session, doctor_id=doctor_id, patient_id=row.patient_id
    )
    if patient is None:
        raise TherapyNotFoundError(row.patient_id)
    reports = [
        r for r in await list_reports(session, row.patient_id) if r.prescription_id == row.id
    ]
    if not reports:
        raise TherapyConflictError(NO_REPORTS)
    doctor = await identity.get_user(session, doctor_id)
    drug = catalog.drugs.get(row.drug_id)
    snapshot = row.card_snapshot
    text = draft_text(
        Draft(
            trade_name=snapshot["trade_name"],
            inn=snapshot["inn"],
            reg_number=drug.registration.number if drug else None,
            additional_monitoring=drug.additional_monitoring if drug else False,
            indication_title=snapshot["indication_title"],
            dose_mg=row.dose_mg,
            times_per_day=row.times_per_day,
            schedule=row.schedule,
            started_on=row.started_at.astimezone(views.MOSCOW).date(),
            stopped_on=row.stopped_at.astimezone(views.MOSCOW).date() if row.stopped_at else None,
            stop_reason=row.stop_reason,
            course_end=views.ends_on(row),
            off_label_reason=row.off_label_reason if row.off_label else None,
            reports=reports,
            medications=await safety.list_medications(session, row.patient_id),
            sex=patient.sex,
            birth_date=patient.birth_date,
            doctor_name=doctor.full_name if doctor else "",
            today=today,
        )
    )
    return AdverseEventDraftOut(prescription_id=row.id, patient_id=row.patient_id, text=text)


def _snapshot(reaction: AdverseReaction) -> ReportedReaction:
    return ReportedReaction(
        id=reaction.id,
        title=reaction.title,
        patient_title=reaction.patient_title or reaction.title,
        level=reaction.level,
    )


def _titles(titles: Iterable[str]) -> str:
    """«Скованность мышц», «Головная боль» → «скованность мышц, головная боль»."""
    return ", ".join(t[:1].lower() + t[1:] for t in titles)


def _times(n: int) -> str:
    """«1 раз», «2 раза», «5 раз», «12 раз»."""
    few = 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14
    return f"{n} раза" if few else f"{n} раз"


def _unique(refs: Iterable[SourceRef]) -> list[SourceRef]:
    return list({(r.source, r.locator): r for r in refs}.values())


def _moscow(report: ReactionReportOut) -> date:
    return report.reported_at.astimezone(views.MOSCOW).date()


def _report_line(r: ReactionReportOut) -> str:
    line = f"- {_moscow(r):%d.%m.%Y}: {_titles(x.title for x in r.reactions)}"
    return f"{line}. Со слов пациента: «{r.note}»" if r.note else line


def _medication_line(m: MedicationOut) -> str:
    name = f"{m.name} ({m.inn})" if m.inn else m.name
    since = f", с {m.started_on:%d.%m.%Y}" if m.started_on else ""
    until = f", по {m.stopped_on:%d.%m.%Y}" if m.stopped_on else ""
    return f"- {name}{since}{until}"
