"""Контракт идемпотентной записи оценок по доступным шкалам."""

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from structlog.testing import capture_logs
from tests.assessments.conftest import (
    BASE,
    AssessmentCare,
    AssessmentNotifications,
    MakeAssessmentCare,
    login,
    submission,
)
from tests.conftest import MakeUser

from clin_compass.modules.alerts.models import Alert
from clin_compass.modules.assessments.models import AssessmentResult
from clin_compass.modules.assessments.service import ScaleCatalog
from clin_compass.modules.identity.schemas import Role

PRIMARY_SOURCE = {
    "id": "test-scale-validation",
    "title": "Тестовая русскоязычная валидация",
    "url": "https://example.org/test-scale-validation",
    "supplementary": False,
}
SUPPLEMENTARY_SOURCE = {
    "id": "test-validation",
    "title": "Тестовая дополнительная валидация",
    "url": "https://example.org/test-validation",
    "supplementary": True,
}
BAND_LABELS = {"low": "Низкий", "high": "Высокий"}


# Пороги суммы total выдуманной шкалы test-eligible: tests/assessments/knowledge/scales.
@pytest.mark.parametrize(
    ("answers", "expected_total", "expected_band", "source"),
    [
        ({"q1": 0, "q2": 0}, 0, "low", ("test-scale-validation", "с. 3")),
        ({"q1": 1, "q2": 0}, 1, "low", ("test-scale-validation", "с. 3")),
        ({"q1": 1, "q2": 1}, 2, "high", ("test-scale-validation", "с. 4")),
    ],
    ids=["минимум", "верхняя-граница-low", "нижняя-граница-high"],
)
async def test_submission_scores_multiple_sums_and_returns_source_snapshots(
    assessment_client: AsyncClient,
    assessment_care: AssessmentCare,
    answers: dict[str, int],
    expected_total: int,
    expected_band: str,
    source: tuple[str, str],
) -> None:
    await login(assessment_client, assessment_care.patient.email)

    response = await assessment_client.post(
        BASE,
        json=submission(str(assessment_care.patient.id), answers=answers),
    )

    assert response.status_code == 201
    result = response.json()
    assert result["patient_id"] == str(assessment_care.patient.id)
    assert result["scale_title"] == "Тестовая шкала для пациента"
    expected_time = datetime.fromisoformat("2026-10-17T14:00:00+03:00").astimezone(UTC)
    actual_time = datetime.fromisoformat(result["completed_at"]).astimezone(UTC)
    assert actual_time == expected_time
    assert result["received_at"]
    assert result["answers"] == answers
    total = result["scores"]["total"]
    assert total["value"] == expected_total
    assert total["band"]["code"] == expected_band
    assert total["band"]["label"] == BAND_LABELS[expected_band]
    assert (total["band"]["source"]["id"], total["band"]["source"]["locator"]) == source
    assert total["band"]["source"] == {"locator": source[1], **PRIMARY_SOURCE}
    assert result["scores"]["first"]["value"] == answers["q1"]
    assert result["sources"] == [PRIMARY_SOURCE, SUPPLEMENTARY_SOURCE]


@pytest.mark.parametrize(
    "answers",
    [
        {"q1": 0},
        {"q1": 0, "q2": 0, "unknown": 0},
        {"q1": 2, "q2": 0},
    ],
)
async def test_submission_rejects_missing_extra_or_unavailable_answers(
    assessment_client: AsyncClient, assessment_care: AssessmentCare, answers: dict[str, int]
) -> None:
    await login(assessment_client, assessment_care.patient.email)

    response = await assessment_client.post(
        BASE,
        json=submission(str(assessment_care.patient.id), answers=answers),
    )

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_answers"


@pytest.mark.parametrize("value", [True, 1.5, 1.0])
async def test_submission_rejects_non_integer_answer_as_a_structural_422(
    assessment_client: AsyncClient, assessment_care: AssessmentCare, value: object
) -> None:
    await login(assessment_client, assessment_care.patient.email)
    body = submission(str(assessment_care.patient.id))
    body["answers"] = {"q1": value, "q2": 0}

    response = await assessment_client.post(BASE, json=body)

    assert response.status_code == 422
    assert "code" not in response.json()


@pytest.mark.parametrize(
    "scale_id", ["missing-scale", "test-draft", "test-unlicensed", "test-clinician"]
)
async def test_submission_rejects_unavailable_scales_with_stable_code(
    assessment_client: AsyncClient, assessment_care: AssessmentCare, scale_id: str
) -> None:
    await login(assessment_client, assessment_care.patient.email)

    response = await assessment_client.post(
        BASE, json=submission(str(assessment_care.patient.id), scale_id=scale_id)
    )

    assert response.status_code == 404
    assert response.json()["code"] == "scale_unavailable"


async def test_submission_rejects_another_patient_id(
    assessment_client: AsyncClient, assessment_care: AssessmentCare
) -> None:
    await login(assessment_client, assessment_care.patient.email)

    response = await assessment_client.post(BASE, json=submission(str(uuid4())))

    assert response.status_code == 403
    assert response.json()["code"] == "patient_mismatch"
    assert response.json()["detail"] == "Patient identifier does not match session"


async def test_submission_rejects_doctor_with_patient_mismatch_without_side_effects(
    assessment_client: AsyncClient,
    assessment_care: AssessmentCare,
    assessment_notifications: AssessmentNotifications,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    body = submission(str(assessment_care.patient.id))

    unauthenticated = await assessment_client.post(BASE, json=body)
    assert unauthenticated.status_code == 401

    await login(assessment_client, assessment_care.doctor.email)
    response = await assessment_client.post(BASE, json=body)

    assert response.status_code == 403
    assert response.json()["code"] == "patient_mismatch"
    assert response.json()["detail"] == "Only patients submit assessments"
    assert await assessment_notifications.settle() == []
    async with sessionmaker() as session:
        result_count = await session.scalar(select(func.count()).select_from(AssessmentResult))
        alert_count = await session.scalar(select(func.count()).select_from(Alert))
    assert result_count == alert_count == 0


async def test_submission_without_lead_doctor_is_rejected_before_writing(
    assessment_client: AsyncClient,
    make_user: MakeUser,
    assessment_notifications: AssessmentNotifications,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    # Пациент, созданный через CLI без приглашения врача: сигнал по результату некому адресовать.
    patient = await make_user(email="orphan-patient@example.com", role=Role.PATIENT)
    await login(assessment_client, patient.email)

    response = await assessment_client.post(
        BASE, json=submission(str(patient.id), answers={"q1": 1, "q2": 1})
    )

    assert response.status_code == 409
    assert response.json()["code"] == "lead_doctor_missing"
    assert await assessment_notifications.settle() == []
    async with sessionmaker() as session:
        result_count = await session.scalar(select(func.count()).select_from(AssessmentResult))
    assert result_count == 0


async def test_submission_rejects_a_stale_scale_version_with_the_current_version(
    assessment_client: AsyncClient, assessment_care: AssessmentCare
) -> None:
    await login(assessment_client, assessment_care.patient.email)

    response = await assessment_client.post(
        BASE,
        json=submission(str(assessment_care.patient.id), scale_version=2),
    )

    assert response.status_code == 409
    assert response.json()["code"] == "scale_version_changed"
    assert response.json()["current_version"] == 1


async def test_submission_replay_precedes_catalogue_and_changed_body_conflicts(
    assessment_client: AsyncClient, assessment_app: FastAPI, assessment_care: AssessmentCare
) -> None:
    await login(assessment_client, assessment_care.patient.email)
    body = submission(str(assessment_care.patient.id), completed_at="2026-10-17T11:00:00Z")
    first = await assessment_client.post(BASE, json=body)
    assert first.status_code == 201

    catalog = assessment_app.state.assessment_catalog
    scale = catalog.scales["test-eligible"].model_copy(
        update={"version": 2, "title": "Обновлённая тестовая шкала"}
    )
    sources = dict(catalog.sources)
    sources["test-validation"] = sources["test-validation"].model_copy(
        update={"title": "Обновлённый источник"}
    )
    assessment_app.state.assessment_catalog = ScaleCatalog(
        scales={**catalog.scales, "test-eligible": scale}, sources=sources
    )

    replay_body = {**body, "completed_at": "2026-10-17T14:00:00+03:00"}
    replay = await assessment_client.post(BASE, json=replay_body)
    changed = await assessment_client.post(
        BASE,
        json={**body, "answers": {"q1": 1, "q2": 0}},
    )

    assert replay.status_code == 200
    assert replay.json() == first.json()
    assert changed.status_code == 409
    assert changed.json()["code"] == "submission_conflict"


async def test_same_version_catalogue_mutation_is_rejected_across_patients_but_replay_survives(
    assessment_client: AsyncClient,
    assessment_app: FastAPI,
    assessment_care: AssessmentCare,
    make_assessment_care: MakeAssessmentCare,
) -> None:
    other = await make_assessment_care(
        "immutable-other-doctor@example.com", "immutable-other-patient@example.com"
    )
    await login(assessment_client, assessment_care.patient.email)
    original_body = submission(str(assessment_care.patient.id))
    first = await assessment_client.post(BASE, json=original_body)
    assert first.status_code == 201

    catalog = assessment_app.state.assessment_catalog
    original_scale = catalog.scales["test-eligible"]
    changed_item = original_scale.items[0].model_copy(update={"text": "Изменённый смысл вопроса"})
    changed_scale = original_scale.model_copy(
        update={"items": (changed_item, *original_scale.items[1:])}
    )
    assessment_app.state.assessment_catalog = ScaleCatalog(
        scales={**catalog.scales, "test-eligible": changed_scale}, sources=catalog.sources
    )

    replay = await assessment_client.post(BASE, json=original_body)
    await login(assessment_client, other.patient.email)
    with capture_logs() as logs:
        new_patient_submission = await assessment_client.post(
            BASE, json=submission(str(other.patient.id))
        )

    assert replay.status_code == 200
    assert replay.json() == first.json()
    assert new_patient_submission.status_code == 503
    assert new_patient_submission.json()["code"] == "scale_definition_conflict"
    conflicts = [entry for entry in logs if entry["event"] == "assessments.definition_conflict"]
    assert conflicts == [
        {
            "event": "assessments.definition_conflict",
            "log_level": "error",
            "scale_id": "test-eligible",
            "scale_version": 1,
        }
    ]


async def test_new_scale_version_can_establish_a_new_immutable_definition(
    assessment_client: AsyncClient,
    assessment_app: FastAPI,
    assessment_care: AssessmentCare,
) -> None:
    await login(assessment_client, assessment_care.patient.email)
    original = await assessment_client.post(BASE, json=submission(str(assessment_care.patient.id)))
    assert original.status_code == 201

    catalog = assessment_app.state.assessment_catalog
    previous = catalog.scales["test-eligible"]
    changed_item = previous.items[0].model_copy(update={"text": "Новая версия вопроса"})
    new_version = previous.model_copy(
        update={"version": 2, "items": (changed_item, *previous.items[1:])}
    )
    assessment_app.state.assessment_catalog = ScaleCatalog(
        scales={**catalog.scales, "test-eligible": new_version}, sources=catalog.sources
    )

    accepted = await assessment_client.post(
        BASE,
        json=submission(str(assessment_care.patient.id), scale_version=2),
    )

    assert accepted.status_code == 201
    assert accepted.json()["scale_version"] == 2


async def test_same_submission_replayed_concurrently_creates_one_result_and_event(
    assessment_client: AsyncClient,
    assessment_care: AssessmentCare,
    assessment_notifications: AssessmentNotifications,
) -> None:
    await login(assessment_client, assessment_care.patient.email)
    body = submission(str(assessment_care.patient.id))

    first, second = await asyncio.gather(
        assessment_client.post(BASE, json=body),
        assessment_client.post(BASE, json=body),
    )

    assert sorted([first.status_code, second.status_code]) == [200, 201]
    assert first.json()["id"] == second.json()["id"]
    [event] = await assessment_notifications.wait_for(1)
    assert event == {
        "to": [str(assessment_care.patient.id), str(assessment_care.doctor.id)],
        "type": "assessment.completed",
        "data": {"assessment_id": first.json()["id"]},
    }
    assert await assessment_notifications.settle() == [event]
