"""Публичный интерфейс модуля therapy (спека #28): подбор по справочнику, предложения,
назначения и их отмена; варианты по клиническим рекомендациям (спека #107).

Предложения создаёт только код по справочнику `knowledge/drugs/`. Назначение и отмена
применяются appliers `proposals` в транзакции решения врача (`appliers.py`). Коммитит
вызывающий.
"""

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from clin_compass.modules.patients import service as patients
from clin_compass.modules.patients.schemas import PatientSummary
from clin_compass.modules.proposals import service as proposals
from clin_compass.modules.proposals.schemas import ProposalOut
from clin_compass.modules.proposals.service import ApplierRegistry
from clin_compass.modules.therapy import checks, guidance, safety, views
from clin_compass.modules.therapy.checks import Catalog, Profile, Regimen
from clin_compass.modules.therapy.models import Prescription, PrescriptionAppearance
from clin_compass.modules.therapy.schemas import (
    AppearanceState,
    AppearanceUpdateIn,
    GuidanceOut,
    IntakeSchemeSnapshot,
    MedicationIn,
    MedicationOut,
    OptionsOut,
    PrescriptionOut,
    RegimenIn,
    ScheduleItem,
)

PRESCRIPTION_KIND = "therapy.prescription"
STOP_KIND = "therapy.stop"
NO_DRUG = "В справочнике нет препарата по диагнозам пациента"
REVISED = "изменено врачом"
PROFILE_CHANGED = "профиль изменился"
DOCTOR_TIMES = "время приёма определяет врач"
MIN_OFF_LABEL_REASON = 10
NOT_FAILED = "Повторить можно только справку, которую посчитать не удалось"


class TherapyNotFoundError(Exception):
    """Предложения или назначения нет, или врач не ведущий у пациента.

    `patient_id` — пациент найденной записи: отказ по владению пишется в журнал доступа.
    """

    def __init__(self, patient_id: UUID | None = None) -> None:
        super().__init__("therapy record not found")
        self.patient_id = patient_id


class TherapyConflictError(Exception):
    """Действие невозможно в текущем состоянии. Текст показывается врачу."""


class OffLabelReasonRequiredError(Exception):
    """Схема вне инструкции без обоснования врача."""


def moscow_today() -> date:
    return datetime.now(views.MOSCOW).date()


async def options(
    session: AsyncSession, catalog: Catalog, *, patient: PatientSummary, today: date
) -> OptionsOut:
    """Проверить препараты справочника по профилю и предложить те, что прошли проверки.

    Открытое предложение того же препарата по тому же показанию не дублируется. Подбор
    одного пациента идёт по очереди: у таблицы предложений нет уникального ограничения.
    """
    await lock(session, f"therapy.options:{patient.id}")
    # `patient` прочитан до блокировки. Профиль, сохранённый, пока подбор её ждал,
    # `reject_open` уже не застанет: проверяем по перечитанному.
    profile = await current_profile(
        session, patient_id=patient.id, doctor_id=patient.lead_doctor_id, today=today
    )
    found = checks.candidates(catalog, profile)
    if not found:
        return OptionsOut(options=[], message=NO_DRUG)
    open_ = {
        p.origin_ref: p
        for p in await proposals.list_open(session, patient_id=patient.id, kind=PRESCRIPTION_KIND)
    }
    result = []
    for drug, indication in found:
        run = checks.run_checks(drug, indication, profile, catalog, today)
        proposal: ProposalOut | None = None
        if checks.can_propose(run):
            ref = f"{drug.id}/{indication.id}"
            proposal = open_.get(ref) or await proposals.create_proposal(
                session,
                patient_id=patient.id,
                kind=PRESCRIPTION_KIND,
                payload=views.payload(catalog, drug, indication, run),
                origin_type="rule",
                origin_ref=ref,
            )
        result.append(views.option(catalog, drug, indication, run, proposal))
    return OptionsOut(options=result, message=None)


async def revise(
    session: AsyncSession,
    registry: ApplierRegistry,
    catalog: Catalog,
    *,
    doctor_id: UUID,
    proposal_id: UUID,
    regimen: RegimenIn,
    comment: str | None,
) -> PrescriptionOut:
    """Подтвердить предложение со схемой врача.

    Та же схема — обычное подтверждение. Другая — предложение системы отклоняется
    («изменено врачом»), предложение врача с его схемой подтверждается. Вне инструкции —
    только с обоснованием.
    """
    original = await proposals.get_proposal(session, proposal_id)
    if original is None or original.kind != PRESCRIPTION_KIND:
        raise TherapyNotFoundError
    if await patients.lead_doctor_of(session, original.patient_id) != doctor_id:
        raise TherapyNotFoundError(original.patient_id)
    drug = catalog.drugs.get(original.payload["drug_id"])
    indication = views.indication_of(drug, original.payload["indication_id"]) if drug else None
    if drug is None or indication is None:
        raise TherapyConflictError("Препарата или показания больше нет в справочнике")
    chosen = Regimen(regimen.dose_mg, regimen.times_per_day, regimen.duration_days)
    label_schedule = indication.regimen.schedule
    same_times = chosen.times_per_day == indication.regimen.times_per_day
    schedule = regimen.schedule or (label_schedule if same_times else DOCTOR_TIMES)
    off_label = checks.is_off_label(indication, chosen)
    reason = (comment or "").strip()
    if off_label and len(reason) < MIN_OFF_LABEL_REASON:
        raise OffLabelReasonRequiredError
    decided = original
    if chosen != Regimen.of(indication) or schedule != label_schedule:
        await proposals.decide(
            session,
            registry,
            doctor_id=doctor_id,
            proposal_id=original.id,
            confirm=False,
            comment=REVISED,
        )
        payload = dict(original.payload)
        payload["regimen"] = {**payload["regimen"], **views.regimen_fields(chosen)}
        payload["regimen"]["schedule"] = schedule
        days = chosen.duration_days
        payload["regimen"]["duration_text"] = f"{days} дн. (выбрал врач)" if days else "без срока"
        payload["off_label"] = off_label
        payload["off_label_reason"] = reason if off_label else None
        decided = await proposals.create_proposal(
            session,
            patient_id=original.patient_id,
            kind=PRESCRIPTION_KIND,
            payload=payload,
            origin_type="doctor",
            origin_ref=str(original.id),
        )
    await proposals.decide(
        session,
        registry,
        doctor_id=doctor_id,
        proposal_id=decided.id,
        confirm=True,
        comment=reason or None,
    )
    row = await session.scalar(select(Prescription).where(Prescription.proposal_id == decided.id))
    if row is None:
        raise RuntimeError("prescription applier did not create a row")
    return views.prescription_out(row, moscow_today())


async def stop(
    session: AsyncSession,
    registry: ApplierRegistry,
    *,
    doctor_id: UUID,
    prescription_id: UUID,
    reason: str,
    today: date,
) -> PrescriptionOut:
    """Отмена — предложение `therapy.stop` врача, подтверждённое в том же действии."""
    row = await session.get(Prescription, prescription_id)
    if row is None:
        raise TherapyNotFoundError
    if await patients.lead_doctor_of(session, row.patient_id) != doctor_id:
        raise TherapyNotFoundError(row.patient_id)
    if views.status_of(row, today) != "active":
        raise TherapyConflictError("Назначение уже не действует")
    proposal = await proposals.create_proposal(
        session,
        patient_id=row.patient_id,
        kind=STOP_KIND,
        payload={"prescription_id": str(row.id), "reason": reason, "drug_id": row.drug_id},
        origin_type="doctor",
        origin_ref=str(row.id),
    )
    await proposals.decide(
        session,
        registry,
        doctor_id=doctor_id,
        proposal_id=proposal.id,
        confirm=True,
        comment=reason,
    )
    await session.refresh(row)
    return views.prescription_out(row, today)


async def reject_open(session: AsyncSession, *, patient_id: UUID, doctor_id: UUID) -> None:
    """Профиль изменился: открытые предложения назначений проверены по старым данным.

    Та же блокировка, что у подбора: иначе подбор по старым диагнозам закоммитит
    предложение после того, как здесь его ещё не было.
    """
    await lock(session, f"therapy.options:{patient_id}")
    for proposal in await proposals.list_open(
        session, patient_id=patient_id, kind=PRESCRIPTION_KIND
    ):
        await proposals.reject(
            session, doctor_id=doctor_id, proposal_id=proposal.id, comment=PROFILE_CHANGED
        )


async def add_medication(
    session: AsyncSession,
    catalog: Catalog,
    patient_id: UUID,
    body: MedicationIn,
    *,
    doctor_id: UUID,
) -> MedicationOut:
    """Препарат вне системы (спека #29). Проверки после него вызывающий запускает сам."""
    return await safety.add_medication(session, catalog, patient_id, body, doctor_id=doctor_id)


async def list_medications(session: AsyncSession, patient_id: UUID) -> list[MedicationOut]:
    return await safety.list_medications(session, patient_id)


async def data_changed(
    session: AsyncSession, catalog: Catalog, *, patient_id: UUID, doctor_id: UUID, today: date
) -> None:
    """Профиль, диагнозы или препараты пациента изменились.

    Открытые предложения проверены по старым данным — отклоняются. Действующие назначения
    проверяются заново: ухудшение по правилу безопасности — сигнал врачу. Справка по КР
    ставится в расчёт, если сменился ключ профиля.
    """
    await reject_open(session, patient_id=patient_id, doctor_id=doctor_id)
    patient = await patients.get_patient_for_doctor(
        session, doctor_id=doctor_id, patient_id=patient_id
    )
    if patient is not None:
        await safety.recheck(session, catalog, patient=patient, today=today)
        await request_guidance(session, catalog, patient=patient, today=today)


async def request_guidance(
    session: AsyncSession,
    catalog: Catalog,
    *,
    patient: PatientSummary,
    today: date,
    retry_failed: bool = False,
) -> None:
    """Поставить расчёт вариантов по КР для текущего профиля (спека #107, 4.4).

    Нет активных диагнозов — ничего. `retry_failed` возвращает в расчёт строку `failed`
    текущего ключа: так делает сид. Доступ проверяет вызывающий.
    """
    profile = await guidance.profile_of(session, catalog, patient, today)
    if profile is not None:
        await guidance.request(session, patient.id, profile, retry_failed=retry_failed)


async def get_guidance(
    session: AsyncSession, catalog: Catalog, *, patient: PatientSummary, today: date
) -> GuidanceOut:
    """Варианты по КР для врача. Ключ сменился без правки (справочник, возраст) — расчёт
    ставится здесь. Доступ проверяет вызывающий."""
    profile = await guidance.profile_of(session, catalog, patient, today)
    if profile is not None:
        await guidance.request(session, patient.id, profile)
    return await guidance.view(session, catalog, patient.id, profile)


async def retry_guidance(
    session: AsyncSession, catalog: Catalog, *, patient: PatientSummary, today: date
) -> GuidanceOut:
    """Справку, которую посчитать не удалось, — снова в расчёт.

    Raises:
        TherapyConflictError: Нет диагнозов или расчёт не в статусе `failed`.
    """
    profile = await guidance.profile_of(session, catalog, patient, today)
    try:
        await guidance.retry(session, patient.id, profile)
    except guidance.GuidanceConflictError:
        raise TherapyConflictError(NOT_FAILED) from None
    return await guidance.view(session, catalog, patient.id, profile)


async def list_prescriptions(
    session: AsyncSession, patient_id: UUID, today: date
) -> list[PrescriptionOut]:
    """Все назначения пациента, старые первыми. Доступ проверяет вызывающий."""
    rows = await session.scalars(
        select(Prescription)
        .where(Prescription.patient_id == patient_id)
        .order_by(Prescription.started_at)
    )
    return [views.prescription_out(r, today) for r in rows]


async def schedule(
    session: AsyncSession, catalog: Catalog, patient_id: UUID, today: date
) -> list[ScheduleItem]:
    """Действующая схема приёма для самого пациента и реакции для отметки (спека #29)."""
    rows = list(
        await session.scalars(
            select(Prescription)
            .where(Prescription.patient_id == patient_id)
            .order_by(Prescription.started_at)
        )
    )
    active = [row for row in rows if views.status_of(row, today) == "active"]
    if not active:
        return []
    overrides = {
        row.prescription_id: row
        for row in await session.scalars(
            select(PrescriptionAppearance).where(
                PrescriptionAppearance.prescription_id.in_([row.id for row in active])
            )
        )
    }
    result = []
    for row in active:
        drug = catalog.drugs.get(row.drug_id)
        state = views.appearance_state(catalog, row, overrides.get(row.id))
        result.append(
            ScheduleItem(
                **state.model_dump(),
                prescription_id=row.id,
                trade_name=row.card_snapshot["trade_name"],
                inn=row.card_snapshot["inn"],
                dose_mg=row.dose_mg,
                times_per_day=row.times_per_day,
                schedule=row.schedule,
                started_at=row.started_at,
                ends_on=views.ends_on(row),
                instructions=[i["text"] for i in row.card_snapshot.get("instructions", [])],
                reactions=views.reaction_options(drug) if drug else [],
            )
        )
    return result


async def set_prescription_appearance(
    session: AsyncSession,
    catalog: Catalog,
    *,
    patient_id: UUID,
    prescription_id: UUID,
    body: AppearanceUpdateIn,
    today: date,
) -> AppearanceState:
    """Сохранить внешний вид своего активного назначения с проверкой CAS-версии."""
    row = await session.scalar(
        select(Prescription).where(Prescription.id == prescription_id).with_for_update()
    )
    if row is None:
        raise TherapyNotFoundError()
    if row.patient_id != patient_id:
        raise TherapyNotFoundError(row.patient_id)
    if views.status_of(row, today) != "active":
        raise TherapyConflictError("Вид можно настроить только для действующего назначения")

    shape = body.appearance.shape if body.appearance is not None else None
    color = body.appearance.color if body.appearance is not None else None
    revision: int | None
    if body.expected_revision == 0:
        insert = pg_insert(PrescriptionAppearance).values(
            prescription_id=prescription_id, shape=shape, color=color, revision=1
        )
        insert_statement = insert.on_conflict_do_update(
            index_elements=[PrescriptionAppearance.prescription_id],
            set_={
                "shape": insert.excluded.shape,
                "color": insert.excluded.color,
                "revision": PrescriptionAppearance.revision + 1,
            },
            where=PrescriptionAppearance.revision == 0,
        ).returning(PrescriptionAppearance.revision)
        revision = await session.scalar(insert_statement)
    else:
        update_statement = (
            update(PrescriptionAppearance)
            .where(
                PrescriptionAppearance.prescription_id == prescription_id,
                PrescriptionAppearance.revision == body.expected_revision,
            )
            .values(
                shape=shape,
                color=color,
                revision=body.expected_revision + 1,
            )
            .returning(PrescriptionAppearance.revision)
        )
        revision = await session.scalar(update_statement)
    if revision is None:
        raise TherapyConflictError("Вид таблетки уже изменён. Обновите данные и повторите выбор")
    override = await session.get(PrescriptionAppearance, prescription_id)
    if override is None:
        raise RuntimeError("appearance update did not create a row")
    return views.appearance_state(catalog, row, override)


async def prescription_snapshot_for_date(
    session: AsyncSession,
    *,
    patient_id: UUID,
    prescription_id: UUID,
    scheduled_on: date,
) -> IntakeSchemeSnapshot | None:
    """Вернуть снимок назначения, только если оно действовало в день приёма."""
    row = await session.scalar(
        select(Prescription).where(
            Prescription.id == prescription_id,
            Prescription.patient_id == patient_id,
        )
    )
    if row is None:
        return None
    started_on = row.started_at.astimezone(views.MOSCOW).date()
    ends_on = views.ends_on(row)
    stopped_on = row.stopped_at.astimezone(views.MOSCOW).date() if row.stopped_at else None
    if (
        scheduled_on < started_on
        or (ends_on is not None and scheduled_on >= ends_on)
        or (stopped_on is not None and scheduled_on >= stopped_on)
    ):
        return None
    snapshot = row.card_snapshot
    return IntakeSchemeSnapshot(
        prescription_id=row.id,
        trade_name=snapshot["trade_name"],
        inn=snapshot["inn"],
        dose_mg=row.dose_mg,
        times_per_day=row.times_per_day,
        schedule=row.schedule,
        started_on=started_on,
        ends_on=ends_on,
        stopped_on=stopped_on,
    )


async def current_profile(
    session: AsyncSession, *, patient_id: UUID, doctor_id: UUID, today: date
) -> Profile:
    """Профиль для проверки по текущим данным: пациент читается здесь заново."""
    patient = await patients.get_patient_for_doctor(
        session, doctor_id=doctor_id, patient_id=patient_id
    )
    if patient is None:
        raise RuntimeError("patient of a therapy check not found")
    return await safety.load_profile(session, patient, today)


async def lock(session: AsyncSession, key: str) -> None:
    """Транзакционная блокировка по ключу: подбор и правки профиля одного пациента — по очереди."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": key}
    )


async def seed_prescription(
    session: AsyncSession,
    catalog: Catalog,
    *,
    patient: PatientSummary,
    started_at: datetime,
    today: date,
) -> UUID | None:
    """Назначение для демо (`make seed`, спека #34, раздел 8): подбор, подтверждение ведущим
    врачом и начало в `started_at`. Есть назначение — его id; подбор без предложения — None.
    """
    found = await list_prescriptions(session, patient.id, today)
    if found:
        return found[-1].id
    offer = next(
        (
            o.proposal
            for o in (await options(session, catalog, patient=patient, today=today)).options
            if o.proposal
        ),
        None,
    )
    if offer is None:
        return None
    # appliers импортирует этот модуль: импорт здесь, иначе цикл.
    from clin_compass.modules.therapy import appliers

    registry = ApplierRegistry()
    appliers.register_appliers(registry, catalog)
    await proposals.decide(
        session,
        registry,
        doctor_id=patient.lead_doctor_id,
        proposal_id=offer.id,
        confirm=True,
        comment=None,
    )
    row = await session.scalar(select(Prescription).where(Prescription.proposal_id == offer.id))
    if row is None:
        raise RuntimeError("confirmed seed proposal has no prescription")
    row.started_at = started_at
    await session.flush()
    return row.id
