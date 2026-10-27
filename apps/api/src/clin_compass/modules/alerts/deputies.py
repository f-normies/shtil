"""Назначения заместителей врачей администратором (спека #56, разделы 2 и 3).

Назначение и отзыв одного врача идут по очереди: пересечение периодов проверяется под
advisory-блокировкой по врачу. Отзыв закрывает доступы заместителя к пациентам врача.
Коммитит вызывающий.
"""

from datetime import date, datetime, time, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from clin_compass.modules.alerts import escalation
from clin_compass.modules.alerts.escalation import Assignment, AssignmentError
from clin_compass.modules.alerts.models import Deputy
from clin_compass.modules.alerts.schemas import AdminDoctorOut, DeputyIn, DeputyOut
from clin_compass.modules.identity import service as identity
from clin_compass.modules.identity.schemas import Role
from clin_compass.modules.patients import service as patients


class DeputyNotFoundError(Exception):
    """Назначения нет."""


async def on_day(session: AsyncSession, doctor_id: UUID, day: date) -> Deputy | None:
    """Действующее назначение заместителя врача на день: то же правило, что
    `escalation.active_deputy`, запросом. Блокировка держится до коммита выдачи доступа:
    отзыв либо увидит выданный доступ, либо завершится до чтения назначения.

    Порядок worker: сигнал → назначение → доступ. Отзыв берёт назначение → доступ
    и никогда не блокирует сигнал, поэтому обратного порядка блокировок нет.
    """
    await _lock(session, doctor_id)
    return await session.scalar(
        select(Deputy).where(
            Deputy.doctor_id == doctor_id,
            Deputy.revoked_at.is_(None),
            Deputy.starts_on <= day,
            Deputy.ends_on >= day,
        )
    )


async def list_current(session: AsyncSession, today: date) -> list[DeputyOut]:
    """Действующие и будущие назначения: неотозванные, конец не раньше сегодня."""
    rows = await session.scalars(
        select(Deputy)
        .where(Deputy.revoked_at.is_(None), Deputy.ends_on >= today)
        .order_by(Deputy.starts_on, Deputy.created_at)
    )
    return await _out(session, list(rows))


async def doctors(session: AsyncSession, today: date) -> list[AdminDoctorOut]:
    """Врачи организации и их заместитель на сегодня; без заместителя — `no_deputy`."""
    current = {d.doctor_id: d for d in await list_current(session, today) if d.starts_on <= today}
    return [
        AdminDoctorOut(
            id=u.id,
            full_name=u.full_name,
            deputy=current.get(u.id),
            no_deputy=u.id not in current,
        )
        for u in await identity.list_users(session, Role.DOCTOR)
    ]


async def assign(session: AsyncSession, admin_id: UUID, body: DeputyIn) -> DeputyOut:
    """Назначить заместителя.

    Raises:
        AssignmentError: Не врач, врач сам себе заместитель, неверный период.
        AssignmentConflictError: Пересечение с действующим назначением врача.
    """
    users = {u.id: u for u in await identity.get_users(session, [body.doctor_id, body.deputy_id])}
    if any(u.role != Role.DOCTOR for u in users.values()) or len(users) < len(
        {body.doctor_id, body.deputy_id}
    ):
        raise AssignmentError("Назначить можно только врача организации")
    await _lock(session, body.doctor_id)
    existing = await session.scalars(select(Deputy).where(Deputy.doctor_id == body.doctor_id))
    new = Assignment(
        id=UUID(int=0),
        doctor_id=body.doctor_id,
        deputy_id=body.deputy_id,
        starts_on=body.starts_on,
        ends_on=body.ends_on,
        revoked=False,
    )
    escalation.check_assignment(new, [_assignment(r) for r in existing])
    row = Deputy(
        doctor_id=body.doctor_id,
        deputy_id=body.deputy_id,
        starts_on=body.starts_on,
        ends_on=body.ends_on,
        basis=body.basis.strip(),
        assigned_by=admin_id,
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)
    [out] = await _out(session, [row])
    return out


async def revoke(session: AsyncSession, admin_id: UUID, assignment_id: UUID) -> DeputyOut:
    """Отозвать назначение. Повтор ничего не меняет. Нет назначения — `DeputyNotFoundError`."""
    row = await session.get(Deputy, assignment_id)
    if row is None:
        raise DeputyNotFoundError
    await _lock(session, row.doctor_id)
    await session.refresh(row)
    if row.revoked_at is None:
        row.revoked_at, row.revoked_by = func.now(), admin_id
        await patients.end_deputy_access(
            session,
            deputy_id=row.deputy_id,
            lead_doctor_id=row.doctor_id,
            granted_from=_day_start(row.starts_on),
            granted_before=_day_start(row.ends_on + timedelta(days=1)),
        )
        await session.flush()
        await session.refresh(row)
    [out] = await _out(session, [row])
    return out


def _day_start(day: date) -> datetime:
    return datetime.combine(day, time(0, 0), escalation.MOSCOW)


async def _lock(session: AsyncSession, doctor_id: UUID) -> None:
    key = func.hashtextextended(f"alerts.deputies:{doctor_id}", 0)
    await session.execute(select(func.pg_advisory_xact_lock(key)))


def _assignment(row: Deputy) -> Assignment:
    return Assignment(
        id=row.id,
        doctor_id=row.doctor_id,
        deputy_id=row.deputy_id,
        starts_on=row.starts_on,
        ends_on=row.ends_on,
        revoked=row.revoked_at is not None,
    )


async def _out(session: AsyncSession, rows: list[Deputy]) -> list[DeputyOut]:
    ids = sorted({i for r in rows for i in (r.doctor_id, r.deputy_id)})
    names = {u.id: u.full_name for u in await identity.get_users(session, ids)}
    return [
        DeputyOut(
            id=r.id,
            doctor_id=r.doctor_id,
            doctor_name=names.get(r.doctor_id, ""),
            deputy_id=r.deputy_id,
            deputy_name=names.get(r.deputy_id, ""),
            starts_on=r.starts_on,
            ends_on=r.ends_on,
            basis=r.basis,
            created_at=r.created_at,
            revoked_at=r.revoked_at,
        )
        for r in rows
    ]
