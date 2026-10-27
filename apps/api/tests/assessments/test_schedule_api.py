"""Расписание опросов через API: пациент, ведущий врач, чужой врач (спека #111)."""

from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.assessments.conftest import (
    BASE,
    AssessmentCare,
    MakeAssessmentCare,
    login,
    submission,
)

from clin_compass.modules.assessments.service import ScaleCatalog
from clin_compass.modules.identity.models import AccessLogEntry
from clin_compass.modules.patients.models import Patient

MOSCOW = ZoneInfo("Europe/Moscow")
SCHEDULE = f"{BASE}/schedule"


def today() -> date:
    return datetime.now(MOSCOW).date()


def at_noon(day: date) -> str:
    return datetime.combine(day, time(12), MOSCOW).isoformat()


async def register_on(
    sessionmaker: async_sessionmaker[AsyncSession], patient_id: UUID, day: date
) -> None:
    """Сдвинуть день регистрации синтетического пациента в прошлое."""
    async with sessionmaker() as session:
        moment = datetime.combine(day, time(9), MOSCOW).astimezone(UTC)
        await session.execute(
            update(Patient).where(Patient.user_id == patient_id).values(created_at=moment)
        )
        await session.commit()


async def answer_once(client: AsyncClient, care: AssessmentCare, day: date) -> str:
    await login(client, care.patient.email)
    response = await client.post(
        BASE, json=submission(str(care.patient.id), completed_at=at_noon(day))
    )
    assert response.status_code == 201
    return str(response.json()["id"])


async def actions(sessionmaker: async_sessionmaker[AsyncSession], actor: UUID) -> list[str]:
    async with sessionmaker() as session:
        rows = await session.scalars(
            select(AccessLogEntry.action).where(AccessLogEntry.actor_id == actor)
        )
        return [row for row in rows if row.startswith("assessments.schedule")]


async def test_patient_gets_due_day_of_scheduled_scales(
    assessment_client: AsyncClient,
    assessment_care: AssessmentCare,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    # Регистрация 20 дней назад, ответ на следующий день: плановый день R+15 пропущен,
    # пациенту он предлагается и дальше (спека #111, «Пациент»).
    registered = today() - timedelta(days=20)
    await register_on(sessionmaker, assessment_care.patient.id, registered)
    await answer_once(assessment_client, assessment_care, registered + timedelta(days=1))

    response = await assessment_client.get(SCHEDULE)

    assert response.status_code == 200
    assert response.json() == {
        "today": today().isoformat(),
        "items": [
            {
                "scale_id": "test-eligible",
                "scale_title": "Тестовая шкала для пациента",
                "due_on": (registered + timedelta(days=15)).isoformat(),
            }
        ],
    }
    assert await actions(sessionmaker, assessment_care.patient.id) == ["assessments.schedule"]


async def test_new_patient_is_due_on_registration_day(
    assessment_client: AsyncClient, assessment_care: AssessmentCare
) -> None:
    await login(assessment_client, assessment_care.patient.email)

    response = await assessment_client.get(SCHEDULE)

    assert [item["due_on"] for item in response.json()["items"]] == [today().isoformat()]


async def test_doctor_gets_planned_dates_and_denied_access_is_logged(
    assessment_client: AsyncClient,
    assessment_care: AssessmentCare,
    make_assessment_care: MakeAssessmentCare,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    foreign = await make_assessment_care(
        "schedule-foreign-doctor@example.com", "schedule-foreign-patient@example.com"
    )
    registered = today() - timedelta(days=20)
    await register_on(sessionmaker, assessment_care.patient.id, registered)
    answer_id = await answer_once(
        assessment_client, assessment_care, registered + timedelta(days=1)
    )

    await login(assessment_client, assessment_care.doctor.email)
    path = f"/api/v1/patients/{assessment_care.patient.id}/assessments/schedule"
    owned = await assessment_client.get(path)
    filtered = await assessment_client.get(path, params={"scale_id": "test-eligible-second"})
    denied = await assessment_client.get(
        f"/api/v1/patients/{foreign.patient.id}/assessments/schedule"
    )

    def planned(offset: int, status: str, assessment_id: str | None = None) -> dict[str, Any]:
        day = (registered + timedelta(days=offset)).isoformat()
        return {"planned_on": day, "status": status, "assessment_id": assessment_id}

    assert owned.status_code == 200
    assert owned.json() == {
        "today": today().isoformat(),
        "items": [
            {
                "scale_id": "test-eligible",
                "period_days": 14,
                "grace_days": 3,
                "planned": [
                    planned(0, "done", answer_id),
                    planned(15, "missed"),
                    planned(29, "upcoming"),
                ],
            }
        ],
    }
    assert filtered.json()["items"] == []
    assert denied.status_code == 404
    assert await actions(sessionmaker, assessment_care.doctor.id) == [
        "assessments.schedule",
        "assessments.schedule",
        "assessments.schedule.denied",
    ]


async def test_draft_schedule_is_not_used(
    assessment_client: AsyncClient, assessment_app: FastAPI, assessment_care: AssessmentCare
) -> None:
    # Приложение использует только проверенные данные: черновик расписания — пропусков нет.
    catalog = assessment_app.state.assessment_catalog
    assert catalog.schedule is not None
    assessment_app.state.assessment_catalog = ScaleCatalog(
        scales=catalog.scales,
        sources=catalog.sources,
        schedule=catalog.schedule.model_copy(update={"status": "draft"}),
    )
    await login(assessment_client, assessment_care.patient.email)

    response = await assessment_client.get(SCHEDULE)

    assert response.json()["items"] == []


async def test_without_schedule_file_items_are_empty(
    empty_assessment_client: AsyncClient, assessment_care: AssessmentCare
) -> None:
    await login(empty_assessment_client, assessment_care.patient.email)

    response = await empty_assessment_client.get(SCHEDULE)

    assert response.status_code == 200
    assert response.json()["items"] == []


async def test_doctor_cannot_read_own_schedule_endpoint(
    assessment_client: AsyncClient, assessment_care: AssessmentCare
) -> None:
    await login(assessment_client, assessment_care.doctor.email)

    response = await assessment_client.get(SCHEDULE)

    assert response.status_code == 403
