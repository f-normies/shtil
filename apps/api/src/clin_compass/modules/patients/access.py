"""Временный доступ заместителя к пациенту по сигналу (спека #56, разделы 3 и 9).

Доступ выдаёт и закрывает модуль alerts через `service.py`. Один доступ — один врач,
один пациент, один сигнал. Действует, пока не закрыт и не истёк; истечение проверяется
при чтении, отдельной задачи нет. Строки не удаляются.
"""

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import ColumnElement, and_, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from clin_compass.modules.patients.models import AccessGrant, Patient
from clin_compass.modules.patients.schemas import AccessEndReason, AccessGrantOut


def grant_active(ended_at: datetime | None, expires_at: datetime, now: datetime) -> bool:
    """Доступ действует: не закрыт и срок не наступил."""
    return ended_at is None and expires_at > now


def _active() -> ColumnElement[bool]:
    return and_(AccessGrant.ended_at.is_(None), AccessGrant.expires_at > func.now())


async def grant_access(
    session: AsyncSession,
    *,
    patient_id: UUID,
    doctor_id: UUID,
    alert_id: UUID,
    expires_at: datetime,
) -> None:
    """Выдать доступ заместителю. Действующий доступ по тому же сигналу не дублируется."""
    existing = await session.scalar(
        select(AccessGrant.id).where(
            AccessGrant.alert_id == alert_id, AccessGrant.doctor_id == doctor_id, _active()
        )
    )
    if existing is None:
        session.add(
            AccessGrant(
                patient_id=patient_id, doctor_id=doctor_id, alert_id=alert_id, expires_at=expires_at
            )
        )
        await session.flush()


async def end_access(session: AsyncSession, *, alert_id: UUID, reason: AccessEndReason) -> None:
    """Закрыть действующие доступы по сигналу: решение или действие ведущего врача."""
    await session.execute(
        update(AccessGrant)
        .where(AccessGrant.alert_id == alert_id, _active())
        .values(ended_at=func.now(), end_reason=reason)
    )


async def end_deputy_access(
    session: AsyncSession,
    *,
    deputy_id: UUID,
    lead_doctor_id: UUID,
    granted_from: datetime,
    granted_before: datetime,
) -> None:
    """Отзыв назначения: закрыть доступы заместителя к пациентам этого врача (`revoked`),
    выданные в период этого назначения. Доступ по другому назначению той же пары остаётся."""
    lead_patients = select(Patient.user_id).where(Patient.lead_doctor_id == lead_doctor_id)
    await session.execute(
        update(AccessGrant)
        .where(
            AccessGrant.doctor_id == deputy_id,
            AccessGrant.patient_id.in_(lead_patients),
            AccessGrant.granted_at >= granted_from,
            AccessGrant.granted_at < granted_before,
            _active(),
        )
        .values(ended_at=func.now(), end_reason="revoked")
    )


async def granted_alerts(
    session: AsyncSession, *, doctor_id: UUID, patient_id: UUID | None = None
) -> list[UUID]:
    """Сигналы, по которым у врача действующий доступ заместителя."""
    query = select(AccessGrant.alert_id).where(AccessGrant.doctor_id == doctor_id, _active())
    if patient_id is not None:
        query = query.where(AccessGrant.patient_id == patient_id)
    return list(await session.scalars(query.order_by(AccessGrant.granted_at)))


async def has_access(session: AsyncSession, *, doctor_id: UUID, patient_id: UUID) -> bool:
    return bool(await granted_alerts(session, doctor_id=doctor_id, patient_id=patient_id))


async def grants_of(session: AsyncSession, alert_ids: Sequence[UUID]) -> dict[UUID, AccessGrantOut]:
    """Последний доступ по каждому сигналу: для «Хода обработки» (спека #56, 9.3)."""
    if not alert_ids:
        return {}
    rows = await session.scalars(
        select(AccessGrant)
        .where(AccessGrant.alert_id.in_(alert_ids))
        .order_by(AccessGrant.granted_at)
    )
    return {r.alert_id: AccessGrantOut.model_validate(r) for r in rows}
