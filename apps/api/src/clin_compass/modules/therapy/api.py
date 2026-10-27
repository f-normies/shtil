"""HTTP-роутер модуля therapy: профиль и диагнозы, подбор, назначения (спека #28, раздел 6),
варианты по клиническим рекомендациям (спека #107, 4.5); нежелательные реакции и черновик
извещения (спека #29).

Ручки профиля и диагнозов живут здесь: их изменение отклоняет открытые предложения терапии,
а модуль `patients` не может вызывать `therapy` (его импортирует `proposals`).

Файл длиннее 400 строк: все ручки модуля — в одном роутере, по одной схеме обработки ошибок.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request

from clin_compass.core.deps import DbSession
from clin_compass.modules.identity import service as identity
from clin_compass.modules.identity.deps import DoctorUser, PatientUser
from clin_compass.modules.patients import service as patients
from clin_compass.modules.patients.deps import owned_patient
from clin_compass.modules.patients.schemas import (
    DiagnosisIn,
    DiagnosisOut,
    PatientSummary,
    ProfileUpdate,
)
from clin_compass.modules.proposals import service as proposals
from clin_compass.modules.therapy import reactions, safety, service
from clin_compass.modules.therapy.deps import DrugCatalog
from clin_compass.modules.therapy.schemas import (
    AdverseEventDraftOut,
    AppearanceState,
    AppearanceUpdateIn,
    DrugLookupOut,
    GuidanceOut,
    MedicationIn,
    MedicationOut,
    MedicationStopIn,
    OptionsOut,
    PrescriptionOut,
    ReactionReportIn,
    ReactionReportOut,
    ReviseIn,
    ScheduleItem,
    StopIn,
)

router = APIRouter(tags=["therapy"])


def _appliers(request: Request) -> proposals.ApplierRegistry:
    registry: proposals.ApplierRegistry = request.app.state.proposal_appliers
    return registry


Appliers = Annotated[proposals.ApplierRegistry, Depends(_appliers)]


async def _denied(
    session: DbSession, actor_id: UUID, action: str, error: service.TherapyNotFoundError
) -> None:
    """Отказ по владению — в журнал доступа, как у `owned_patient`. Записи нет — не пишем."""
    if error.patient_id is None:
        return
    await session.rollback()
    await identity.log_access(
        session, actor_id=actor_id, action=f"{action}.denied", patient_id=error.patient_id
    )
    await session.commit()


async def _changed(
    session: DbSession, catalog: DrugCatalog, patient: PatientSummary, doctor_id: UUID
) -> None:
    await service.data_changed(
        session, catalog, patient_id=patient.id, doctor_id=doctor_id, today=service.moscow_today()
    )


@router.put("/patients/{patient_id}/profile")
async def update_profile(
    body: ProfileUpdate,
    patient: Annotated[PatientSummary, Depends(owned_patient("patient.profile.update"))],
    doctor: DoctorUser,
    session: DbSession,
    catalog: DrugCatalog,
) -> PatientSummary:
    """Дата рождения и пол. Открытые предложения терапии проверены по старым данным."""
    updated = await patients.set_profile(session, patient, body)
    await _changed(session, catalog, patient, doctor.id)
    await session.commit()
    return updated


@router.get("/patients/{patient_id}/diagnoses")
async def list_diagnoses(
    patient: Annotated[
        PatientSummary, Depends(owned_patient("patient.diagnoses.list", deputy=True))
    ],
    session: DbSession,
) -> list[DiagnosisOut]:
    diagnoses = await patients.list_diagnoses(session, patient.id)
    await session.commit()
    return diagnoses


@router.post("/patients/{patient_id}/diagnoses", status_code=201)
async def add_diagnosis(
    body: DiagnosisIn,
    patient: Annotated[PatientSummary, Depends(owned_patient("patient.diagnoses.add"))],
    doctor: DoctorUser,
    session: DbSession,
    catalog: DrugCatalog,
) -> DiagnosisOut:
    """Диагноз ставит врач. Система диагнозов не ставит."""
    diagnosis = await patients.add_diagnosis(session, patient.id, body, doctor_id=doctor.id)
    await _changed(session, catalog, patient, doctor.id)
    await session.commit()
    return diagnosis


@router.post("/patients/{patient_id}/diagnoses/{diagnosis_id}/resolve")
async def resolve_diagnosis(
    diagnosis_id: UUID,
    patient: Annotated[PatientSummary, Depends(owned_patient("patient.diagnoses.resolve"))],
    doctor: DoctorUser,
    session: DbSession,
    catalog: DrugCatalog,
) -> DiagnosisOut:
    try:
        diagnosis = await patients.resolve_diagnosis(
            session, patient.id, diagnosis_id, doctor_id=doctor.id
        )
    except patients.DiagnosisNotFoundError:
        await session.commit()
        raise HTTPException(status_code=404, detail="Diagnosis not found") from None
    await _changed(session, catalog, patient, doctor.id)
    await session.commit()
    return diagnosis


@router.post("/patients/{patient_id}/therapy/options")
async def therapy_options(
    patient: Annotated[PatientSummary, Depends(owned_patient("patient.therapy.options"))],
    session: DbSession,
    catalog: DrugCatalog,
) -> OptionsOut:
    """Проверить препараты справочника по профилю и предложить прошедшие проверки."""
    result = await service.options(session, catalog, patient=patient, today=service.moscow_today())
    await session.commit()
    return result


@router.get("/patients/{patient_id}/therapy/guidance")
async def therapy_guidance(
    patient: Annotated[PatientSummary, Depends(owned_patient("patient.therapy.guidance"))],
    session: DbSession,
    catalog: DrugCatalog,
) -> GuidanceOut:
    """Варианты терапии по КР: готовый ответ, статус расчёта и устарел ли ответ. Это справка."""
    result = await service.get_guidance(
        session, catalog, patient=patient, today=service.moscow_today()
    )
    await session.commit()
    return result


@router.post("/patients/{patient_id}/therapy/guidance/retry")
async def retry_therapy_guidance(
    patient: Annotated[PatientSummary, Depends(owned_patient("patient.therapy.guidance.retry"))],
    session: DbSession,
    catalog: DrugCatalog,
) -> GuidanceOut:
    """Справку, которую посчитать не удалось, — снова в расчёт. Не `failed` — 409."""
    try:
        result = await service.retry_guidance(
            session, catalog, patient=patient, today=service.moscow_today()
        )
    except service.TherapyConflictError as error:
        await session.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from None
    await session.commit()
    return result


@router.post("/therapy/proposals/{proposal_id}/revise")
async def revise_proposal(
    proposal_id: UUID,
    body: ReviseIn,
    doctor: DoctorUser,
    session: DbSession,
    registry: Appliers,
    catalog: DrugCatalog,
) -> PrescriptionOut:
    """Подтвердить предложение назначения со своей схемой."""
    try:
        prescription = await service.revise(
            session,
            registry,
            catalog,
            doctor_id=doctor.id,
            proposal_id=proposal_id,
            regimen=body.regimen,
            comment=body.comment,
        )
    except service.TherapyNotFoundError as error:
        await _denied(session, doctor.id, "therapy.revise", error)
        raise HTTPException(status_code=404, detail="Proposal not found") from None
    except proposals.ProposalNotFoundError:
        raise HTTPException(status_code=404, detail="Proposal not found") from None
    except proposals.ProposalAlreadyDecidedError:
        raise HTTPException(status_code=409, detail="Proposal is already decided") from None
    except service.OffLabelReasonRequiredError:
        raise HTTPException(
            status_code=422, detail="Off-label regimen needs a reason of 10+ characters"
        ) from None
    except (service.TherapyConflictError, proposals.ProposalApplyError) as error:
        await session.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from None
    await identity.log_access(
        session, actor_id=doctor.id, action="therapy.revise", patient_id=prescription.patient_id
    )
    await session.commit()
    return prescription


@router.get("/patients/{patient_id}/prescriptions")
async def list_prescriptions(
    patient: Annotated[
        PatientSummary, Depends(owned_patient("patient.prescriptions.list", deputy=True))
    ],
    session: DbSession,
) -> list[PrescriptionOut]:
    result = await service.list_prescriptions(session, patient.id, service.moscow_today())
    await session.commit()
    return result


@router.post("/prescriptions/{prescription_id}/stop")
async def stop_prescription(
    prescription_id: UUID,
    body: StopIn,
    doctor: DoctorUser,
    session: DbSession,
    registry: Appliers,
) -> PrescriptionOut:
    """Отмена назначения врачом с причиной."""
    try:
        prescription = await service.stop(
            session,
            registry,
            doctor_id=doctor.id,
            prescription_id=prescription_id,
            reason=body.reason,
            today=service.moscow_today(),
        )
    except service.TherapyNotFoundError as error:
        await _denied(session, doctor.id, "therapy.stop", error)
        raise HTTPException(status_code=404, detail="Prescription not found") from None
    except (service.TherapyConflictError, proposals.ProposalApplyError) as error:
        await session.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from None
    await identity.log_access(
        session, actor_id=doctor.id, action="therapy.stop", patient_id=prescription.patient_id
    )
    await session.commit()
    return prescription


@router.get("/me/prescriptions")
async def my_prescriptions(
    patient: PatientUser, session: DbSession, catalog: DrugCatalog
) -> list[ScheduleItem]:
    """Действующая схема приёма вошедшего пациента и реакции для отметки."""
    await identity.log_access(
        session, actor_id=patient.id, action="prescriptions.own", patient_id=patient.id
    )
    result = await service.schedule(session, catalog, patient.id, service.moscow_today())
    await session.commit()
    return result


@router.put("/me/prescriptions/{prescription_id}/appearance")
async def update_my_prescription_appearance(
    prescription_id: UUID,
    body: AppearanceUpdateIn,
    patient: PatientUser,
    session: DbSession,
    catalog: DrugCatalog,
) -> AppearanceState:
    """Сохранить личный вид таблетки у своего действующего назначения."""
    try:
        result = await service.set_prescription_appearance(
            session,
            catalog,
            patient_id=patient.id,
            prescription_id=prescription_id,
            body=body,
            today=service.moscow_today(),
        )
    except service.TherapyNotFoundError as error:
        await _denied(session, patient.id, "prescriptions.appearance", error)
        raise HTTPException(status_code=404, detail="Prescription not found") from None
    except service.TherapyConflictError as error:
        await session.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from None
    await identity.log_access(
        session,
        actor_id=patient.id,
        action="prescriptions.appearance",
        patient_id=patient.id,
    )
    await session.commit()
    return result


@router.post("/me/prescriptions/{prescription_id}/reactions", status_code=201)
async def report_reaction(
    prescription_id: UUID,
    body: ReactionReportIn,
    patient: PatientUser,
    session: DbSession,
    catalog: DrugCatalog,
) -> ReactionReportOut:
    """Пациент отмечает нежелательные реакции из перечня; врач получает сигнал безопасности."""
    try:
        result = await reactions.report(
            session,
            catalog,
            patient_id=patient.id,
            prescription_id=prescription_id,
            body=body,
            today=service.moscow_today(),
        )
    except service.TherapyNotFoundError as error:
        await _denied(session, patient.id, "prescriptions.reaction", error)
        raise HTTPException(status_code=404, detail="Prescription not found") from None
    except service.TherapyConflictError as error:
        await session.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from None
    except reactions.UnknownReactionError as error:
        await session.rollback()
        raise HTTPException(status_code=422, detail=f"Unknown reaction: {error}") from None
    await identity.log_access(
        session, actor_id=patient.id, action="prescriptions.reaction", patient_id=patient.id
    )
    await session.commit()
    return result


@router.get("/patients/{patient_id}/reactions")
async def list_reactions(
    patient: Annotated[
        PatientSummary, Depends(owned_patient("patient.reactions.list", deputy=True))
    ],
    session: DbSession,
) -> list[ReactionReportOut]:
    """Отметки нежелательных реакций пациента по всем назначениям, старые первыми."""
    result = await reactions.list_reports(session, patient.id)
    await session.commit()
    return result


@router.get("/prescriptions/{prescription_id}/adverse-event-draft")
async def adverse_event_draft(
    prescription_id: UUID, doctor: DoctorUser, session: DbSession, catalog: DrugCatalog
) -> AdverseEventDraftOut:
    """Черновик извещения о нежелательной реакции. Без отметок у назначения — 409."""
    try:
        result = await reactions.draft(
            session,
            catalog,
            doctor_id=doctor.id,
            prescription_id=prescription_id,
            today=service.moscow_today(),
        )
    except service.TherapyNotFoundError as error:
        await _denied(session, doctor.id, "therapy.adverse_event_draft", error)
        raise HTTPException(status_code=404, detail="Prescription not found") from None
    except service.TherapyConflictError as error:
        await session.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from None
    await identity.log_access(
        session,
        actor_id=doctor.id,
        action="therapy.adverse_event_draft",
        patient_id=result.patient_id,
    )
    await session.commit()
    return result


@router.get("/therapy/drug-lookup")
async def drug_lookup(name: str, _doctor: DoctorUser, catalog: DrugCatalog) -> DrugLookupOut:
    """Сверка названия препарата со снимком ГРЛС перед внесением."""
    return safety.lookup(catalog, name[:200])


@router.get("/patients/{patient_id}/medications")
async def list_medications(
    patient: Annotated[
        PatientSummary, Depends(owned_patient("patient.medications.list", deputy=True))
    ],
    session: DbSession,
) -> list[MedicationOut]:
    result = await safety.list_medications(session, patient.id)
    await session.commit()
    return result


@router.post("/patients/{patient_id}/medications", status_code=201)
async def add_medication(
    body: MedicationIn,
    patient: Annotated[PatientSummary, Depends(owned_patient("patient.medications.add"))],
    doctor: DoctorUser,
    session: DbSession,
    catalog: DrugCatalog,
) -> MedicationOut:
    """Препарат вне системы. Предложения отклоняются, назначения проверяются заново."""
    medication = await safety.add_medication(
        session, catalog, patient.id, body, doctor_id=doctor.id
    )
    await _changed(session, catalog, patient, doctor.id)
    await session.commit()
    return medication


@router.post("/patients/{patient_id}/medications/{medication_id}/stop")
async def stop_medication(
    medication_id: UUID,
    body: MedicationStopIn,
    patient: Annotated[PatientSummary, Depends(owned_patient("patient.medications.stop"))],
    doctor: DoctorUser,
    session: DbSession,
    catalog: DrugCatalog,
) -> MedicationOut:
    try:
        medication = await safety.stop_medication(
            session, patient.id, medication_id, body.stopped_on
        )
    except safety.MedicationNotFoundError:
        await session.commit()
        raise HTTPException(status_code=404, detail="Medication not found") from None
    except safety.MedicationDatesError:
        await session.commit()
        raise HTTPException(
            status_code=422, detail="stopped_on must not be before started_on"
        ) from None
    await _changed(session, catalog, patient, doctor.id)
    await session.commit()
    return medication
