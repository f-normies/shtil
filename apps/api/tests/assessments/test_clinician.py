"""Шкалы, которые заполняет врач: каталог, отправка, видимость (спека #94)."""

from uuid import uuid4

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.assessments.conftest import (
    BASE,
    AssessmentCare,
    AssessmentNotifications,
    MakeAssessmentCare,
    login,
)

from clin_compass.modules.assessments.models import AssessmentResult
from clin_compass.modules.identity.models import AccessLogEntry

CLINICIAN_SCALES = f"{BASE}/clinician-scales"


def path(care: AssessmentCare) -> str:
    return f"/api/v1/patients/{care.patient.id}/assessments"


def clinician_body(
    *, submission_id: str | None = None, scale_id: str = "test-clinician", q1: int = 1
) -> dict[str, object]:
    return {
        "submission_id": submission_id or str(uuid4()),
        "scale_id": scale_id,
        "scale_version": 1,
        "answers": {"q1": q1},
    }


async def test_doctor_lists_clinician_scales_and_patient_cannot(
    assessment_client: AsyncClient, assessment_care: AssessmentCare
) -> None:
    await login(assessment_client, assessment_care.doctor.email)
    listed = await assessment_client.get(CLINICIAN_SCALES)
    definition = await assessment_client.get(f"{BASE}/scales/test-clinician")

    await login(assessment_client, assessment_care.patient.email)
    as_patient = await assessment_client.get(CLINICIAN_SCALES)
    patient_definition = await assessment_client.get(f"{BASE}/scales/test-clinician")

    assert listed.status_code == 200
    # Шкалы пациента, черновик и шкала без лицензии в список шкал врача не входят.
    assert [item["scale"]["id"] for item in listed.json()["items"]] == ["test-clinician"]
    assert definition.status_code == 200
    assert definition.json()["scale"]["respondent"] == "clinician"
    assert as_patient.status_code == 403
    assert patient_definition.status_code == 404


async def test_doctor_records_result_once_with_author(
    assessment_client: AsyncClient, assessment_care: AssessmentCare
) -> None:
    await login(assessment_client, assessment_care.doctor.email)
    body = clinician_body()

    created = await assessment_client.post(path(assessment_care), json=body)
    replay = await assessment_client.post(path(assessment_care), json=body)
    conflict = await assessment_client.post(
        path(assessment_care), json={**body, "answers": {"q1": 0}}
    )
    # Время ставит сервер: та же оценка с новым ключом — новая запись (спека #94).
    repeated = await assessment_client.post(path(assessment_care), json=clinician_body())
    history = await assessment_client.get(path(assessment_care))

    assert created.status_code == 201
    result = created.json()
    assert result["recorded_by"] == str(assessment_care.doctor.id)
    assert result["patient_id"] == str(assessment_care.patient.id)
    assert result["scores"]["total"]["value"] == 1
    assert replay.status_code == 200
    assert replay.json()["id"] == result["id"]
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "submission_conflict"
    assert repeated.status_code == 201
    assert repeated.json()["id"] != result["id"]
    assert {item["id"] for item in history.json()["items"]} == {
        result["id"],
        repeated.json()["id"],
    }


async def test_doctor_submission_is_checked_against_clinician_scales(
    assessment_client: AsyncClient, assessment_care: AssessmentCare
) -> None:
    await login(assessment_client, assessment_care.doctor.email)

    patient_scale = await assessment_client.post(
        path(assessment_care), json=clinician_body(scale_id="test-eligible")
    )
    incomplete = await assessment_client.post(
        path(assessment_care), json={**clinician_body(), "answers": {}}
    )

    assert patient_scale.status_code == 404
    assert patient_scale.json()["code"] == "scale_unavailable"
    assert incomplete.status_code == 422
    assert incomplete.json()["code"] == "invalid_answers"


async def test_foreign_doctor_and_patient_cannot_record(
    assessment_client: AsyncClient,
    assessment_care: AssessmentCare,
    make_assessment_care: MakeAssessmentCare,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    foreign = await make_assessment_care(
        "clinician-foreign-doctor@example.com", "clinician-foreign-patient@example.com"
    )
    await login(assessment_client, foreign.doctor.email)
    denied = await assessment_client.post(path(assessment_care), json=clinician_body())
    await login(assessment_client, assessment_care.patient.email)
    as_patient = await assessment_client.post(path(assessment_care), json=clinician_body())

    assert denied.status_code == 404
    assert as_patient.status_code == 403
    async with sessionmaker() as session:
        actions = list(
            await session.scalars(
                select(AccessLogEntry.action).where(AccessLogEntry.actor_id == foreign.doctor.id)
            )
        )
        results = list(await session.scalars(select(AssessmentResult.id)))
    assert actions == ["assessments.create.denied"]
    assert results == []


async def test_patient_does_not_see_clinician_results(
    assessment_client: AsyncClient,
    assessment_care: AssessmentCare,
    assessment_notifications: AssessmentNotifications,
) -> None:
    await login(assessment_client, assessment_care.doctor.email)
    created = await assessment_client.post(path(assessment_care), json=clinician_body())
    assert created.status_code == 201

    await login(assessment_client, assessment_care.patient.email)
    own = await assessment_client.get(BASE)

    assert own.json()["items"] == []
    events = await assessment_notifications.wait_for(1)
    completed = [event for event in events if event["type"] == "assessment.completed"]
    assert [event["to"] for event in completed] == [[str(assessment_care.doctor.id)]]
