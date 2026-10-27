"""История результатов, снимки определений и журналирование чтения."""

from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.assessments.conftest import (
    BASE,
    AssessmentCare,
    MakeAssessmentCare,
    login,
    submission,
)

from clin_compass.modules.assessments import service
from clin_compass.modules.assessments.service import ScaleCatalog
from clin_compass.modules.identity.models import AccessLogEntry
from clin_compass.modules.patients import service as patients


async def test_history_paginates_by_received_id_and_keeps_snapshots_after_catalogue_change(
    assessment_client: AsyncClient,
    assessment_app: FastAPI,
    assessment_care: AssessmentCare,
) -> None:
    await login(assessment_client, assessment_care.patient.email)
    created = []
    for index, answers in enumerate([{"q1": 0, "q2": 0}, {"q1": 1, "q2": 0}, {"q1": 1, "q2": 1}]):
        response = await assessment_client.post(
            BASE,
            json=submission(
                str(assessment_care.patient.id),
                answers=answers,
                completed_at=f"2026-10-17T{14 - index:02d}:00:00Z",
            ),
        )
        assert response.status_code == 201
        created.append(response.json())

    catalog = assessment_app.state.assessment_catalog
    scale = catalog.scales["test-eligible"].model_copy(
        update={"version": 2, "title": "Новая версия шкалы"}
    )
    sources = dict(catalog.sources)
    sources["test-validation"] = sources["test-validation"].model_copy(
        update={"title": "Изменившийся источник"}
    )
    assessment_app.state.assessment_catalog = ScaleCatalog(
        scales={**catalog.scales, "test-eligible": scale}, sources=sources
    )

    first_page = await assessment_client.get(BASE, params={"limit": 2})
    second_page = await assessment_client.get(
        BASE, params={"limit": 2, "before": first_page.json()["next_cursor"]}
    )
    filtered = await assessment_client.get(BASE, params={"scale_id": "test-eligible"})
    invalid_cursor = await assessment_client.get(BASE, params={"before": "not-a-uuid"})

    assert first_page.status_code == 200
    assert created[2]["completed_at"] < created[1]["completed_at"]
    assert [item["id"] for item in first_page.json()["items"]] == [
        created[2]["id"],
        created[1]["id"],
    ]
    assert first_page.json()["next_cursor"] == created[1]["id"]
    assert [item["id"] for item in second_page.json()["items"]] == [created[0]["id"]]
    assert second_page.json()["next_cursor"] is None
    assert len(filtered.json()["items"]) == 3
    assert invalid_cursor.status_code == 422
    historical = first_page.json()["items"][0]
    assert historical["scale_title"] == "Тестовая шкала для пациента"
    titles = {source["id"]: source["title"] for source in historical["sources"]}
    assert titles["test-validation"] == "Тестовая дополнительная валидация"


async def test_patient_history_is_scoped_to_current_patient_and_read_is_journaled(
    assessment_client: AsyncClient,
    assessment_care: AssessmentCare,
    make_assessment_care: MakeAssessmentCare,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    other = await make_assessment_care(
        "history-other-doctor@example.com", "history-other-patient@example.com"
    )
    await login(assessment_client, assessment_care.patient.email)
    submitted = await assessment_client.post(
        BASE,
        json=submission(str(assessment_care.patient.id)),
    )
    assert submitted.status_code == 201
    own = await assessment_client.get(BASE)

    await login(assessment_client, other.patient.email)
    other_history = await assessment_client.get(BASE, params={"before": submitted.json()["id"]})

    assert [item["id"] for item in own.json()["items"]] == [submitted.json()["id"]]
    assert other_history.status_code == 200
    assert other_history.json() == {"items": [], "next_cursor": None}
    async with sessionmaker() as session:
        logs = list(
            (
                await session.scalars(
                    select(AccessLogEntry).where(
                        AccessLogEntry.actor_id == assessment_care.patient.id,
                        AccessLogEntry.action == "assessments.list",
                    )
                )
            ).all()
        )
    assert len(logs) == 1
    assert logs[0].patient_id == assessment_care.patient.id


async def test_doctor_can_read_owned_patient_history_and_denied_access_is_logged(
    assessment_client: AsyncClient,
    assessment_care: AssessmentCare,
    make_assessment_care: MakeAssessmentCare,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    foreign = await make_assessment_care(
        "history-foreign-doctor@example.com", "history-foreign-patient@example.com"
    )
    await login(assessment_client, assessment_care.patient.email)
    submitted = await assessment_client.post(
        BASE,
        json=submission(str(assessment_care.patient.id)),
    )
    assert submitted.status_code == 201

    await login(assessment_client, assessment_care.doctor.email)
    owned = await assessment_client.get(
        f"/api/v1/patients/{assessment_care.patient.id}/assessments"
    )
    denied = await assessment_client.get(f"/api/v1/patients/{foreign.patient.id}/assessments")

    assert owned.status_code == 200
    assert [item["id"] for item in owned.json()["items"]] == [submitted.json()["id"]]
    assert denied.status_code == 404
    async with sessionmaker() as session:
        actions = list(
            (
                await session.execute(
                    select(AccessLogEntry.patient_id, AccessLogEntry.action).where(
                        AccessLogEntry.actor_id == assessment_care.doctor.id,
                        AccessLogEntry.patient_id.in_(
                            [assessment_care.patient.id, foreign.patient.id]
                        ),
                    )
                )
            ).all()
        )
    assert dict(actions) == {
        assessment_care.patient.id: "assessments.list",
        foreign.patient.id: "assessments.list.denied",
    }


async def test_history_access_requires_patient_or_leading_doctor(
    assessment_client: AsyncClient, assessment_care: AssessmentCare
) -> None:
    anonymous = await assessment_client.get(BASE)
    await login(assessment_client, assessment_care.doctor.email)
    doctor_as_patient = await assessment_client.get(BASE)
    await login(assessment_client, assessment_care.patient.email)
    patient_as_doctor = await assessment_client.get(
        f"/api/v1/patients/{assessment_care.patient.id}/assessments"
    )

    assert anonymous.status_code == 401
    assert doctor_as_patient.status_code == 403
    assert patient_as_doctor.status_code == 403


async def test_all_results_of_owned_patient_are_listed_by_completion_time(
    assessment_client: AsyncClient,
    assessment_care: AssessmentCare,
    make_assessment_care: MakeAssessmentCare,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    other = await make_assessment_care("other-doctor@example.com", "other-patient@example.com")
    for care, hour in ((assessment_care, 14), (assessment_care, 9), (other, 11)):
        await login(assessment_client, care.patient.email)
        response = await assessment_client.post(
            BASE,
            json=submission(str(care.patient.id), completed_at=f"2026-10-17T{hour:02d}:00:00Z"),
        )
        assert response.status_code == 201

    async with sessionmaker() as session:
        patient = await patients.get_patient_for_doctor(
            session, doctor_id=assessment_care.doctor.id, patient_id=assessment_care.patient.id
        )
        assert patient is not None
        results = await service.list_all_for_owned_patient(session, patient)

    assert [r.completed_at.hour for r in results] == [9, 14]
    assert {r.patient_id for r in results} == {assessment_care.patient.id}
