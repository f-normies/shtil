"""Внутренняя реализация общих настроек пациента и принятого согласия."""

from datetime import UTC, datetime, time
from typing import Any, cast
from uuid import UUID

from sqlalchemy import func, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from clin_compass.modules.identity.schemas import UserOut
from clin_compass.modules.patients.models import Patient, PatientPreferences
from clin_compass.modules.patients.schemas import (
    MoodPalette,
    PatientPreferencesOut,
    PatientPreferencesUpdate,
    StoryMode,
    TextSize,
)


class PatientNotFoundError(Exception):
    """Для вошедшей учётной записи не найдена запись пациента."""


class PreferencesRevisionConflictError(Exception):
    """Переданная версия настроек устарела."""


async def get_preferences(
    session: AsyncSession, patient: UserOut, *, timezone: str
) -> PatientPreferencesOut:
    """Вернуть настройки пациента или виртуальные начальные значения."""
    await _require_patient(session, patient.id)
    row = await session.get(PatientPreferences, patient.id)
    if row is None:
        return _defaults(patient.full_name, timezone)
    return _to_preferences_out(row, timezone)


async def update_preferences(
    session: AsyncSession,
    patient: UserOut,
    request: PatientPreferencesUpdate,
    *,
    timezone: str,
) -> PatientPreferencesOut:
    """Сохранить частичное изменение настроек через атомарное сравнение версии."""
    return await _save_preferences(session, patient, request, timezone=timezone, complete=False)


async def complete_preferences(
    session: AsyncSession,
    patient: UserOut,
    request: PatientPreferencesUpdate,
    *,
    timezone: str,
) -> PatientPreferencesOut:
    """Сохранить настройки и установить время первого завершения онбординга."""
    return await _save_preferences(session, patient, request, timezone=timezone, complete=True)


async def _save_preferences(
    session: AsyncSession,
    patient: UserOut,
    request: PatientPreferencesUpdate,
    *,
    timezone: str,
    complete: bool,
) -> PatientPreferencesOut:
    await _require_patient(session, patient.id)
    values = request.model_dump(exclude={"expected_revision"}, exclude_unset=True)
    if "reminder_time" in values:
        values["reminder_time"] = time.fromisoformat(values["reminder_time"])

    now = datetime.now(UTC)
    update_values: dict[str, Any] = {
        **values,
        "revision": PatientPreferences.revision + 1,
        "updated_at": now,
    }
    if complete:
        update_values["onboarding_completed_at"] = func.coalesce(
            PatientPreferences.onboarding_completed_at, now
        )

    if request.expected_revision == 0:
        insert_values: dict[str, Any] = {
            "patient_id": patient.id,
            "display_name": patient.full_name,
            "reminder_time": time(21, 0),
            "mood_palette": "warm",
            "story_mode": "voice",
            "text_size": "normal",
            "onboarding_completed_at": now if complete else None,
            "revision": 1,
            "updated_at": now,
            **values,
        }
        insert_statement = pg_insert(PatientPreferences).values(**insert_values)
        insert_statement = insert_statement.on_conflict_do_update(
            index_elements=[PatientPreferences.patient_id],
            set_=update_values,
            where=PatientPreferences.revision == request.expected_revision,
        )
        row = await session.scalar(insert_statement.returning(PatientPreferences))
    else:
        update_statement = (
            update(PatientPreferences)
            .where(
                PatientPreferences.patient_id == patient.id,
                PatientPreferences.revision == request.expected_revision,
            )
            .values(**update_values)
        )
        row = await session.scalar(update_statement.returning(PatientPreferences))
    if row is None:
        raise PreferencesRevisionConflictError
    return _to_preferences_out(row, timezone)


async def _require_patient(session: AsyncSession, patient_id: UUID) -> None:
    if await session.get(Patient, patient_id) is None:
        raise PatientNotFoundError


def _defaults(display_name: str, timezone: str) -> PatientPreferencesOut:
    return PatientPreferencesOut(
        display_name=display_name,
        reminder_time="21:00",
        mood_palette="warm",
        story_mode="voice",
        text_size="normal",
        onboarding_completed_at=None,
        revision=0,
        updated_at=None,
        timezone=timezone,
    )


def _to_preferences_out(row: PatientPreferences, timezone: str) -> PatientPreferencesOut:
    return PatientPreferencesOut(
        display_name=row.display_name,
        reminder_time=row.reminder_time.strftime("%H:%M"),
        mood_palette=cast("MoodPalette", row.mood_palette),
        story_mode=cast("StoryMode", row.story_mode),
        text_size=cast("TextSize", row.text_size),
        onboarding_completed_at=row.onboarding_completed_at,
        revision=row.revision,
        updated_at=row.updated_at,
        timezone=timezone,
    )
