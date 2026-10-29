"""Опросы пациента: врач включает шкалу расписания и выбирает период (спека #180).

`schedule.py` считает даты, этот модуль хранит выбор врача. Допустимые периоды и значения
по умолчанию — данные `knowledge/scale-schedule.yaml`.
"""

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from uuid import UUID

import structlog
from clin_compass_knowledge.schemas.scale_schedule import ScheduledScale
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from clin_compass.modules.assessments.catalog import AssessmentError, ScaleCatalog
from clin_compass.modules.assessments.models import PatientScaleSetting
from clin_compass.modules.assessments.schemas import (
    ScaleSettingIn,
    ScaleSettingOut,
    ScaleSettingsOut,
)
from clin_compass.modules.identity import service as identity
from clin_compass.modules.identity.schemas import UserOut
from clin_compass.modules.patients.schemas import PatientSummary

log = structlog.get_logger()


def scheduled(catalog: ScaleCatalog) -> list[ScheduledScale]:
    """Шкалы расписания, которые выдаются пациенту. Черновик расписания не действует."""
    schedule = catalog.schedule
    if schedule is None or schedule.status != "reviewed":
        return []
    return [
        entry
        for entry in schedule.scales
        if entry.scale in catalog.scales and catalog.is_available(catalog.scales[entry.scale])
    ]


def allowed_periods(catalog: ScaleCatalog) -> tuple[int, ...]:
    """Периоды, из которых врач выбирает частоту опроса."""
    return catalog.schedule.allowed_period_days if catalog.schedule is not None else ()


def period_of(catalog: ScaleCatalog, entry: ScheduledScale, row: PatientScaleSetting | None) -> int:
    """Период шкалы у пациента. Период, которого больше нет среди допустимых, не действует."""
    if row is not None and row.period_days in allowed_periods(catalog):
        return row.period_days
    return entry.period_days


async def rows_for(session: AsyncSession, patient_id: UUID) -> dict[str, PatientScaleSetting]:
    """Настройки шкал пациента, которые врач менял, по id шкалы."""
    rows = await session.scalars(
        select(PatientScaleSetting).where(PatientScaleSetting.patient_id == patient_id)
    )
    return {row.scale_id: row for row in rows}


async def patient_settings(
    session: AsyncSession, catalog: ScaleCatalog, patient: PatientSummary
) -> ScaleSettingsOut:
    """Настройки шкал расписания пациента. Владение проверяет `owned_patient`."""
    rows = await rows_for(session, patient.id)
    names = await _names(session, rows.values())
    return ScaleSettingsOut(
        allowed_period_days=list(allowed_periods(catalog)),
        items=[_out(catalog, entry, rows.get(entry.scale), names) for entry in scheduled(catalog)],
    )


async def update_setting(
    session: AsyncSession,
    catalog: ScaleCatalog,
    patient: PatientSummary,
    doctor: UserOut,
    scale_id: str,
    body: ScaleSettingIn,
) -> ScaleSettingOut:
    """Сохранить выбор врача. Те же значения ничего не меняют: время изменения прежнее."""
    entry = next((e for e in scheduled(catalog) if e.scale == scale_id), None)
    if entry is None:
        raise AssessmentError(404, "scale_not_scheduled", "Scale is not in the survey schedule")
    allowed = allowed_periods(catalog)
    if body.period_days not in allowed:
        raise AssessmentError(
            422, "period_not_allowed", "Period is not allowed", allowed_period_days=list(allowed)
        )
    row = await session.get(PatientScaleSetting, (patient.id, scale_id), with_for_update=True)
    current = (row.enabled, period_of(catalog, entry, row)) if row else (True, entry.period_days)
    if current != (body.enabled, body.period_days):
        if row is None:
            row = PatientScaleSetting(patient_id=patient.id, scale_id=scale_id)
            session.add(row)
        row.enabled = body.enabled
        row.period_days = body.period_days
        row.updated_by = doctor.id
        row.updated_at = datetime.now(UTC)
        await session.flush()
        log.info(
            "assessments.scale_setting_changed",
            scale_id=scale_id,
            enabled=body.enabled,
            period_days=body.period_days,
        )
    names = await _names(session, [row] if row else [])
    return _out(catalog, entry, row, names)


async def _names(session: AsyncSession, rows: Iterable[PatientScaleSetting]) -> dict[UUID, str]:
    ids = list({row.updated_by for row in rows})
    if not ids:
        return {}
    return {user.id: user.full_name for user in await identity.get_users(session, ids)}


def _out(
    catalog: ScaleCatalog,
    entry: ScheduledScale,
    row: PatientScaleSetting | None,
    names: Mapping[UUID, str],
) -> ScaleSettingOut:
    return ScaleSettingOut(
        scale_id=entry.scale,
        scale_title=catalog.scales[entry.scale].title,
        enabled=row.enabled if row else True,
        period_days=period_of(catalog, entry, row),
        default_period_days=entry.period_days,
        updated_at=row.updated_at if row else None,
        updated_by=row.updated_by if row else None,
        updated_by_name=names.get(row.updated_by) if row else None,
    )
