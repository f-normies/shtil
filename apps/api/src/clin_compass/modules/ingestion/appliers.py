"""Appliers модуля ingestion: подтверждённый врачом факт — в своё место карточки (спека #30, 3.5).

`proposals.decide` вызывает их в транзакции решения врача. Диагноз пишется в карточку
пациента, текущий препарат и препарат с датой отмены — в препараты `therapy`: по ним работают
проверки безопасности. После них `therapy.data_changed` отклоняет открытые предложения терапии
и перепроверяет назначения. Прошлый препарат без даты отмены, анамнез и фактор риска
пишутся в `ingestion.facts`.
"""

from datetime import date
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from clin_compass.modules.ingestion.analysis import name_key
from clin_compass.modules.ingestion.models import Fact
from clin_compass.modules.ingestion.payloads import is_current
from clin_compass.modules.ingestion.schemas import DIAGNOSIS_KIND, FACT_KIND, MEDICATION_KIND
from clin_compass.modules.patients import service as patients
from clin_compass.modules.patients.schemas import DiagnosisIn
from clin_compass.modules.proposals.schemas import ProposalOut
from clin_compass.modules.proposals.service import ApplierRegistry, ProposalApplyError
from clin_compass.modules.therapy import service as therapy
from clin_compass.modules.therapy.deps import Catalog
from clin_compass.modules.therapy.schemas import MedicationIn

DIAGNOSIS_EXISTS = "Диагноз с этим кодом уже есть в карточке"
MEDICATION_EXISTS = "Препарат уже внесён"
# Длина названия в карточке: `patients.diagnoses.title` и `therapy.medications.name`.
_TITLE_LENGTH = 200


def register(registry: ApplierRegistry, catalog: Catalog) -> None:
    """Три вида предложений. Препарат сверяется с `catalog` — справочником `therapy`."""

    async def apply_diagnosis(session: AsyncSession, proposal: ProposalOut) -> None:
        await _apply_diagnosis(session, catalog, proposal)

    async def apply_medication(session: AsyncSession, proposal: ProposalOut) -> None:
        await _apply_medication(session, catalog, proposal)

    registry.register(DIAGNOSIS_KIND, apply_diagnosis)
    registry.register(MEDICATION_KIND, apply_medication)
    registry.register(FACT_KIND, _apply_fact)


async def _apply_diagnosis(session: AsyncSession, catalog: Catalog, proposal: ProposalOut) -> None:
    doctor_id = _doctor(proposal)
    code = proposal.payload["icd10"]
    # Два одновременных подтверждения одного кода иначе оба прошли бы проверку ниже.
    await therapy.lock(session, f"ingestion.card:{proposal.patient_id}")
    diagnoses = await patients.list_diagnoses(session, proposal.patient_id)
    if any(d.icd10 == code and d.resolved_at is None for d in diagnoses):
        raise ProposalApplyError(DIAGNOSIS_EXISTS)
    title = proposal.payload["text"][:_TITLE_LENGTH]
    await patients.add_diagnosis(
        session, proposal.patient_id, DiagnosisIn(icd10=code, title=title), doctor_id=doctor_id
    )
    await _changed(session, catalog, proposal.patient_id, doctor_id)


async def _apply_medication(session: AsyncSession, catalog: Catalog, proposal: ProposalOut) -> None:
    payload = proposal.payload
    doctor_id = _doctor(proposal)
    stopped_on = date.fromisoformat(payload["stopped_on"]) if payload.get("stopped_on") else None
    if payload["status"] == "past" and stopped_on is None:
        # Без даты отмены окно противопоказания не посчитать: препарат — факт анамнеза.
        await _add_fact(session, proposal, "past_medication")
        return
    name = payload["name"][:_TITLE_LENGTH]
    if payload["status"] == "current":
        await therapy.lock(session, f"ingestion.card:{proposal.patient_id}")
        today = therapy.moscow_today()
        medications = await therapy.list_medications(session, proposal.patient_id)
        if any(
            name_key(m.name) == name_key(name) and is_current(m.stopped_on, today)
            for m in medications
        ):
            raise ProposalApplyError(MEDICATION_EXISTS)
    # МНН и статус в ГРЛС `add_medication` берёт из снимка сам.
    await therapy.add_medication(
        session,
        catalog,
        proposal.patient_id,
        MedicationIn(name=name, stopped_on=stopped_on),
        doctor_id=doctor_id,
    )
    await _changed(session, catalog, proposal.patient_id, doctor_id)


async def _apply_fact(session: AsyncSession, proposal: ProposalOut) -> None:
    await _add_fact(session, proposal, proposal.payload["field"])


async def _add_fact(session: AsyncSession, proposal: ProposalOut, field: str) -> None:
    """Факт в `ingestion.facts`, в транзакции решения врача."""
    if proposal.decided_at is None:
        raise RuntimeError("fact applied without a doctor's decision")
    session.add(
        Fact(
            proposal_id=proposal.id,
            document_id=UUID(proposal.payload["document_id"]),
            patient_id=proposal.patient_id,
            field=field,
            data=proposal.payload,
            confirmed_at=proposal.decided_at,
            confirmed_by=_doctor(proposal),
        )
    )
    await session.flush()


async def _changed(
    session: AsyncSession, catalog: Catalog, patient_id: UUID, doctor_id: UUID
) -> None:
    await therapy.data_changed(
        session, catalog, patient_id=patient_id, doctor_id=doctor_id, today=therapy.moscow_today()
    )


def _doctor(proposal: ProposalOut) -> UUID:
    if proposal.decided_by is None:
        raise RuntimeError("ingestion proposal applied without a doctor's decision")
    return proposal.decided_by
