"""Опросы пациента: врач включает шкалу и выбирает частоту (спека #180)."""

from datetime import date, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

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

from clin_compass.modules.identity.models import AccessLogEntry

MOSCOW = ZoneInfo("Europe/Moscow")
SCALE = "test-eligible"


def today() -> date:
    return datetime.now(MOSCOW).date()


def settings_path(care: AssessmentCare) -> str:
    return f"/api/v1/patients/{care.patient.id}/assessments/settings"


def doctor_schedule_path(care: AssessmentCare) -> str:
    return f"/api/v1/patients/{care.patient.id}/assessments/schedule"


async def actions(sessionmaker: async_sessionmaker[AsyncSession], actor: UUID) -> list[str]:
    async with sessionmaker() as session:
        rows = await session.scalars(
            select(AccessLogEntry.action).where(AccessLogEntry.actor_id == actor)
        )
        return [row for row in rows if row.startswith("assessments.settings")]


async def test_defaults_come_from_knowledge(
    assessment_client: AsyncClient, assessment_care: AssessmentCare
) -> None:
    await login(assessment_client, assessment_care.doctor.email)

    response = await assessment_client.get(settings_path(assessment_care))

    assert response.status_code == 200
    assert response.json() == {
        "allowed_period_days": [7, 14],
        "items": [
            {
                "scale_id": SCALE,
                "scale_title": "Тестовая шкала для пациента",
                "enabled": True,
                "period_days": 14,
                "default_period_days": 14,
                "updated_at": None,
                "updated_by": None,
                "updated_by_name": None,
            }
        ],
    }


async def test_doctor_changes_setting_and_change_is_recorded(
    assessment_client: AsyncClient,
    assessment_care: AssessmentCare,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await login(assessment_client, assessment_care.doctor.email)
    path = f"{settings_path(assessment_care)}/{SCALE}"

    saved = await assessment_client.put(path, json={"enabled": True, "period_days": 7})
    read = await assessment_client.get(settings_path(assessment_care))

    assert saved.status_code == 200
    item = saved.json()
    assert (item["enabled"], item["period_days"]) == (True, 7)
    assert item["updated_by"] == str(assessment_care.doctor.id)
    assert item["updated_by_name"] == "Ведущий врач"
    assert item["updated_at"] is not None
    assert read.json()["items"] == [item]
    assert await actions(sessionmaker, assessment_care.doctor.id) == [
        "assessments.settings.update",
        "assessments.settings",
    ]


async def test_same_values_keep_change_time(
    assessment_client: AsyncClient, assessment_care: AssessmentCare
) -> None:
    await login(assessment_client, assessment_care.doctor.email)
    path = f"{settings_path(assessment_care)}/{SCALE}"

    first = await assessment_client.put(path, json={"enabled": True, "period_days": 7})
    second = await assessment_client.put(path, json={"enabled": True, "period_days": 7})

    assert second.json()["updated_at"] == first.json()["updated_at"]


async def test_foreign_doctor_cannot_read_or_change(
    assessment_client: AsyncClient,
    assessment_care: AssessmentCare,
    make_assessment_care: MakeAssessmentCare,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    foreign = await make_assessment_care(
        "settings-foreign-doctor@example.com", "settings-foreign-patient@example.com"
    )
    await login(assessment_client, foreign.doctor.email)

    read = await assessment_client.get(settings_path(assessment_care))
    changed = await assessment_client.put(
        f"{settings_path(assessment_care)}/{SCALE}", json={"enabled": False, "period_days": 14}
    )

    assert (read.status_code, changed.status_code) == (404, 404)
    assert await actions(sessionmaker, foreign.doctor.id) == [
        "assessments.settings.denied",
        "assessments.settings.update.denied",
    ]


async def test_period_outside_knowledge_and_unscheduled_scale_are_rejected(
    assessment_client: AsyncClient, assessment_care: AssessmentCare
) -> None:
    await login(assessment_client, assessment_care.doctor.email)
    base = settings_path(assessment_care)

    period = await assessment_client.put(
        f"{base}/{SCALE}", json={"enabled": True, "period_days": 10}
    )
    unscheduled = await assessment_client.put(
        f"{base}/test-eligible-second", json={"enabled": True, "period_days": 14}
    )

    assert period.status_code == 422
    assert period.json()["code"] == "period_not_allowed"
    assert unscheduled.status_code == 404
    assert unscheduled.json()["code"] == "scale_not_scheduled"


async def test_disabled_scale_leaves_patient_and_doctor_schedule(
    assessment_client: AsyncClient, assessment_care: AssessmentCare
) -> None:
    await login(assessment_client, assessment_care.doctor.email)
    await assessment_client.put(
        f"{settings_path(assessment_care)}/{SCALE}", json={"enabled": False, "period_days": 14}
    )
    doctor_view = await assessment_client.get(doctor_schedule_path(assessment_care))

    await login(assessment_client, assessment_care.patient.email)
    patient_view = await assessment_client.get(f"{BASE}/schedule")

    assert doctor_view.json()["items"] == []
    assert patient_view.json()["items"] == []


async def test_weekly_period_moves_next_survey(
    assessment_client: AsyncClient, assessment_care: AssessmentCare
) -> None:
    # Ответ в день регистрации: без настроек следующий опрос — через 14 дней.
    await login(assessment_client, assessment_care.patient.email)
    now = datetime.now(MOSCOW) - timedelta(minutes=1)
    answered = await assessment_client.post(
        BASE, json=submission(str(assessment_care.patient.id), completed_at=now.isoformat())
    )
    assert answered.status_code == 201

    await login(assessment_client, assessment_care.doctor.email)
    await assessment_client.put(
        f"{settings_path(assessment_care)}/{SCALE}", json={"enabled": True, "period_days": 7}
    )
    doctor_view = await assessment_client.get(doctor_schedule_path(assessment_care))

    await login(assessment_client, assessment_care.patient.email)
    patient_view = await assessment_client.get(f"{BASE}/schedule")

    expected = now.date() + timedelta(days=7)
    assert doctor_view.json()["items"][0]["period_days"] == 7
    assert [i["due_on"] for i in patient_view.json()["items"]] == [expected.isoformat()]
