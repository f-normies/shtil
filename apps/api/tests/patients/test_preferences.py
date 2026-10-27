"""API for patient preferences, onboarding completion, and accepted consent."""

import asyncio
from datetime import UTC, datetime
from uuid import UUID

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.modules.identity.schemas import Role, UserOut
from clin_compass.modules.patients import service as patients
from clin_compass.modules.patients.models import Consent, Patient
from tests.conftest import MakeUser

PASSWORD = "correct horse battery staple"
PREFERENCES = "/api/v1/me/preferences"
CONSENT = "/api/v1/me/consent"


async def login(client: httpx.AsyncClient, email: str) -> None:
    response = await client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200


async def create_patient(
    client: httpx.AsyncClient,
    make_user: MakeUser,
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    email: str = "patient@example.com",
    full_name: str = "Петрова Анна Сергеевна",
) -> UserOut:
    doctor = await make_user(email="doctor@example.com")
    patient = await make_user(email=email, role=Role.PATIENT, full_name=full_name)
    async with sessionmaker() as session:
        session.add(Patient(user_id=patient.id, lead_doctor_id=doctor.id))
        await session.commit()
    await login(client, email)
    return patient


async def record_consent(
    sessionmaker: async_sessionmaker[AsyncSession],
    patient_id: UUID,
    *,
    version: str,
    given_at: datetime,
) -> None:
    async with sessionmaker() as session:
        session.add(
            Consent(
                patient_id=patient_id,
                kind="personal_data",
                version=version,
                given_at=given_at,
            )
        )
        await session.commit()


async def test_get_preferences_returns_virtual_defaults(
    client: httpx.AsyncClient,
    make_user: MakeUser,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    patient = await create_patient(client, make_user, sessionmaker)

    response = await client.get(PREFERENCES)

    assert response.status_code == 200
    assert response.json() == {
        "display_name": "Петрова Анна Сергеевна",
        "reminder_time": "21:00",
        "mood_palette": "warm",
        "story_mode": "voice",
        "text_size": "normal",
        "onboarding_completed_at": None,
        "revision": 0,
        "updated_at": None,
        "timezone": "Europe/Moscow",
    }
    async with sessionmaker() as session:
        full_name = await session.scalar(
            text("SELECT full_name FROM identity.users WHERE id = :patient_id"),
            {"patient_id": patient.id},
        )
    assert full_name == "Петрова Анна Сергеевна"


async def test_patch_saves_only_submitted_values_and_keeps_registered_name(
    client: httpx.AsyncClient,
    make_user: MakeUser,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    patient = await create_patient(client, make_user, sessionmaker)

    updated = await client.patch(
        PREFERENCES,
        json={"expected_revision": 0, "display_name": "  Анна  ", "mood_palette": "green"},
    )
    current = await client.get(PREFERENCES)

    assert updated.status_code == 200
    assert updated.json()["display_name"] == "Анна"
    assert updated.json()["mood_palette"] == "green"
    assert updated.json()["revision"] == 1
    assert current.status_code == 200
    current_body = current.json()
    assert current_body | {"updated_at": None} == {
        "display_name": "Анна",
        "reminder_time": "21:00",
        "mood_palette": "green",
        "story_mode": "voice",
        "text_size": "normal",
        "onboarding_completed_at": None,
        "revision": 1,
        "updated_at": None,
        "timezone": "Europe/Moscow",
    }
    assert current_body["updated_at"] is not None
    async with sessionmaker() as session:
        full_name = await session.scalar(
            text("SELECT full_name FROM identity.users WHERE id = :patient_id"),
            {"patient_id": patient.id},
        )
    assert full_name == "Петрова Анна Сергеевна"


async def test_first_concurrent_create_and_stale_update_each_have_one_winner(
    client: httpx.AsyncClient,
    make_user: MakeUser,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    patient = await create_patient(client, make_user, sessionmaker)

    first = await asyncio.gather(
        client.patch(PREFERENCES, json={"expected_revision": 0, "mood_palette": "green"}),
        client.patch(PREFERENCES, json={"expected_revision": 0, "mood_palette": "calm"}),
    )

    assert sorted(response.status_code for response in first) == [200, 409]
    first_conflict = next(response for response in first if response.status_code == 409)
    assert first_conflict.headers["content-type"].startswith("application/problem+json")
    assert first_conflict.json()["code"] == "preferences_revision_conflict"
    async with sessionmaker() as session:
        first_revision = await session.scalar(
            text("SELECT revision FROM patients.preferences WHERE patient_id = :patient_id"),
            {"patient_id": patient.id},
        )
    assert first_revision == 1

    second = await asyncio.gather(
        client.patch(PREFERENCES, json={"expected_revision": 1, "reminder_time": "09:00"}),
        client.patch(PREFERENCES, json={"expected_revision": 1, "reminder_time": "14:00"}),
    )

    assert sorted(response.status_code for response in second) == [200, 409]
    second_conflict = next(response for response in second if response.status_code == 409)
    assert second_conflict.json()["code"] == "preferences_revision_conflict"
    current = await client.get(PREFERENCES)
    assert current.status_code == 200
    assert current.json()["revision"] == 2
    assert current.json()["reminder_time"] in {"09:00", "14:00"}


@pytest.mark.parametrize(
    "body",
    [
        {"expected_revision": -1},
        {"expected_revision": True},
        {"expected_revision": "0"},
        {"expected_revision": 0, "patient_id": "00000000-0000-0000-0000-000000000000"},
        {"expected_revision": 0, "display_name": "   "},
        {"expected_revision": 0, "display_name": "x" * 201},
        {"expected_revision": 0, "display_name": None},
        {"expected_revision": 0, "reminder_time": "24:00"},
        {"expected_revision": 0, "reminder_time": "08:07"},
        {"expected_revision": 0, "reminder_time": "8:00"},
        {"expected_revision": 0, "mood_palette": "purple"},
        {"expected_revision": 0, "story_mode": "audio"},
        {"expected_revision": 0, "text_size": "huge"},
    ],
    ids=[
        "negative-revision",
        "boolean-revision",
        "string-revision",
        "client-patient-id",
        "blank-display-name",
        "long-display-name",
        "null-display-name",
        "hour-out-of-range",
        "minute-not-quarter-hour",
        "non-padded-time",
        "unknown-palette",
        "unknown-story-mode",
        "unknown-text-size",
    ],
)
async def test_patch_rejects_invalid_values(
    client: httpx.AsyncClient,
    make_user: MakeUser,
    sessionmaker: async_sessionmaker[AsyncSession],
    body: dict[str, object],
) -> None:
    await create_patient(client, make_user, sessionmaker)

    response = await client.patch(PREFERENCES, json=body)

    assert response.status_code == 422


async def test_complete_saves_settings_once_and_keeps_first_completion_time(
    client: httpx.AsyncClient,
    make_user: MakeUser,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await create_patient(client, make_user, sessionmaker)

    completed = await client.post(
        f"{PREFERENCES}/complete",
        json={"expected_revision": 0, "display_name": "Аня", "reminder_time": "14:00"},
    )
    repeated = await client.post(
        f"{PREFERENCES}/complete", json={"expected_revision": 1, "text_size": "large"}
    )

    assert completed.status_code == 200
    first_completion = completed.json()["onboarding_completed_at"]
    assert first_completion is not None
    assert completed.json()["revision"] == 1
    assert repeated.status_code == 200
    assert repeated.json()["onboarding_completed_at"] == first_completion
    assert repeated.json()["display_name"] == "Аня"
    assert repeated.json()["reminder_time"] == "14:00"
    assert repeated.json()["text_size"] == "large"
    assert repeated.json()["revision"] == 2


async def test_preferences_access_log_contains_metadata_only(
    client: httpx.AsyncClient,
    make_user: MakeUser,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    patient = await create_patient(client, make_user, sessionmaker)

    await client.get(PREFERENCES)
    await client.patch(PREFERENCES, json={"expected_revision": 0, "display_name": "Секретное имя"})

    async with sessionmaker() as session:
        entries = (
            await session.execute(
                text(
                    "SELECT action, patient_id, subject FROM identity.access_log "
                    "WHERE actor_id = :patient_id ORDER BY id"
                ),
                {"patient_id": patient.id},
            )
        ).all()
    assert entries == [
        ("patients.preferences.read", patient.id, None),
        ("patients.preferences.update", patient.id, None),
    ]


@pytest.mark.parametrize("role", [Role.DOCTOR, Role.ADMIN], ids=["doctor", "admin"])
async def test_non_patient_roles_cannot_read_preferences_or_consent(
    client: httpx.AsyncClient, make_user: MakeUser, role: Role
) -> None:
    user = await make_user(email=f"{role.value}@example.com", role=role)
    await login(client, user.email)

    preferences = await client.get(PREFERENCES)
    consent = await client.get(CONSENT)

    assert preferences.status_code == 403
    assert consent.status_code == 403


async def test_preferences_require_authentication(client: httpx.AsyncClient) -> None:
    response = await client.get(PREFERENCES)

    assert response.status_code == 401


async def test_get_consent_returns_own_accepted_version_and_known_text(
    client: httpx.AsyncClient,
    make_user: MakeUser,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    patient = await create_patient(client, make_user, sessionmaker)
    given_at = datetime(2026, 10, 24, 12, 30, tzinfo=UTC)
    document = patients.get_consent_document()
    await record_consent(sessionmaker, patient.id, version=document.version, given_at=given_at)

    response = await client.get(CONSENT)

    assert response.status_code == 200
    assert response.json() == {
        "version": document.version,
        "given_at": given_at.isoformat().replace("+00:00", "Z"),
        "text": document.text,
    }


async def test_get_consent_keeps_unknown_accepted_version_without_substituting_current_text(
    client: httpx.AsyncClient,
    make_user: MakeUser,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    patient = await create_patient(client, make_user, sessionmaker)
    given_at = datetime(2026, 10, 24, 13, 45, tzinfo=UTC)
    await record_consent(sessionmaker, patient.id, version="retired-version", given_at=given_at)

    response = await client.get(CONSENT)

    assert response.status_code == 200
    assert response.json() == {
        "version": "retired-version",
        "given_at": given_at.isoformat().replace("+00:00", "Z"),
        "text": None,
    }


async def test_get_consent_returns_not_found_without_accepted_consent(
    client: httpx.AsyncClient,
    make_user: MakeUser,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await create_patient(client, make_user, sessionmaker)

    response = await client.get(CONSENT)

    assert response.status_code == 404
    assert response.json()["detail"] == "Accepted consent not found"
