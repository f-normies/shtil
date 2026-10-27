"""Appliers модуля therapy (спека #28, раздел 6): назначение и отмена по решению врача.

`proposals.decide` вызывает их в транзакции решения врача, другого пути создать или отменить
назначение нет. Перед назначением проверки повторяются по текущим данным пациента.
"""

from datetime import UTC, date, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from clin_compass.modules.proposals import service as proposals
from clin_compass.modules.proposals.schemas import ProposalOut
from clin_compass.modules.proposals.service import ApplierRegistry, ProposalApplyError
from clin_compass.modules.therapy import checks, safety, service, views
from clin_compass.modules.therapy.checks import Catalog
from clin_compass.modules.therapy.models import Prescription

OTHER_INDICATION = "назначен по другому показанию"
GONE = "Препарата или показания больше нет в справочнике. Подберите терапию заново"
STALE_CARD = "Карточка препарата в справочнике изменилась после подбора. Подберите терапию заново"
FAILED_NOW = "Проверка по текущим данным пациента не пройдена: {failed}. Подберите терапию заново"


def register_appliers(registry: ApplierRegistry, catalog: Catalog) -> None:
    """Назначение и отмена применяются только решением врача через `proposals.decide`.

    Назначение сверяется с `catalog` — справочником, по которому работает подбор.
    """

    async def apply_prescription(session: AsyncSession, proposal: ProposalOut) -> None:
        await _apply_prescription(session, catalog, proposal)

    registry.register(service.PRESCRIPTION_KIND, apply_prescription)
    registry.register(service.STOP_KIND, _apply_stop)


async def _apply_prescription(
    session: AsyncSession, catalog: Catalog, proposal: ProposalOut
) -> None:
    if proposal.decided_by is None:
        raise RuntimeError("prescription applied without a doctor's decision")
    payload = proposal.payload
    # Два одновременных подтверждения одного препарата (по разным показаниям) иначе оба
    # прошли бы проверку ниже: получилось бы два действующих назначения.
    await service.lock(session, f"therapy.drug:{proposal.patient_id}:{payload['drug_id']}")
    active = await session.scalars(
        select(Prescription).where(
            Prescription.patient_id == proposal.patient_id,
            Prescription.drug_id == payload["drug_id"],
            Prescription.stopped_at.is_(None),
        )
    )
    today = service.moscow_today()
    if any(views.status_of(r, today) == "active" for r in active):
        raise ProposalApplyError("Препарат уже назначен: сначала отмените действующее назначение")
    await _recheck(session, catalog, proposal, proposal.decided_by, today)
    regimen = payload["regimen"]
    session.add(
        Prescription(
            patient_id=proposal.patient_id,
            drug_id=payload["drug_id"],
            indication_id=payload["indication_id"],
            dose_mg=regimen["dose_mg"],
            times_per_day=regimen["times_per_day"],
            schedule=regimen["schedule"],
            duration_days=regimen["duration_days"],
            off_label=payload.get("off_label", False),
            off_label_reason=payload.get("off_label_reason"),
            card_snapshot=payload,
            proposal_id=proposal.id,
            confirmed_by=proposal.decided_by,
            safety_state=safety.initial_state(payload["checks"]),
        )
    )
    await session.flush()
    for other in await proposals.list_open(
        session, patient_id=proposal.patient_id, kind=service.PRESCRIPTION_KIND
    ):
        if other.id != proposal.id and other.payload.get("drug_id") == payload["drug_id"]:
            await proposals.reject(
                session,
                doctor_id=proposal.decided_by,
                proposal_id=other.id,
                comment=OTHER_INDICATION,
            )


async def _recheck(
    session: AsyncSession, catalog: Catalog, proposal: ProposalOut, doctor_id: UUID, today: date
) -> None:
    """Проверки предложения сделаны при подборе. С тех пор могли смениться карточка,
    снимок реестра или профиль пациента: подтверждение идёт по текущим данным."""
    payload = proposal.payload
    drug = catalog.drugs.get(payload["drug_id"])
    indication = views.indication_of(drug, payload["indication_id"]) if drug else None
    if drug is None or indication is None:
        raise ProposalApplyError(GONE)
    if payload.get("card_hash") != views.card_hash(drug):
        raise ProposalApplyError(STALE_CARD)
    profile = await service.current_profile(
        session, patient_id=proposal.patient_id, doctor_id=doctor_id, today=today
    )
    run = checks.run_checks(drug, indication, profile, catalog, today)
    failed = "; ".join(f"{c.title} — {c.detail}" for c in run if c.status == "fail")
    if failed:
        raise ProposalApplyError(FAILED_NOW.format(failed=failed))


async def _apply_stop(session: AsyncSession, proposal: ProposalOut) -> None:
    row = await session.get(
        Prescription, UUID(proposal.payload["prescription_id"]), with_for_update=True
    )
    if row is None or row.stopped_at is not None:
        raise ProposalApplyError("Назначение уже отменено")
    row.stopped_at = proposal.decided_at or datetime.now(UTC)
    row.stopped_by = proposal.decided_by
    row.stop_reason = proposal.payload["reason"]
    row.stop_proposal_id = proposal.id
    await session.flush()
