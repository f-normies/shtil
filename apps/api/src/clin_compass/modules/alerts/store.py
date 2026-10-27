"""Чтение и блокировка сигналов, события и сборка ответа: общее для `service` и `handling`."""

from collections.abc import Iterable
from uuid import UUID

from sqlalchemy import Select, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from clin_compass.core import realtime
from clin_compass.modules.alerts.lifecycle import effective_state, moscow_today
from clin_compass.modules.alerts.models import Alert
from clin_compass.modules.alerts.schemas import AlertOut
from clin_compass.modules.identity import service as identity
from clin_compass.modules.patients import service as patients


class AlertNotFoundError(Exception):
    """Сигнала нет, или он адресован другому врачу либо другому пациенту."""


def locked(query: Select[Alert]) -> Select[Alert]:
    # Без populate_existing ORM вернёт объект из сессии со старыми полями, хотя строка
    # заблокирована: подтверждение, сделанное в другой транзакции, было бы не видно.
    return query.with_for_update().execution_options(populate_existing=True)


def with_history() -> Select[Alert]:
    return select(Alert).options(selectinload(Alert.triggers), selectinload(Alert.actions))


async def owned(session: AsyncSession, doctor_id: UUID, alert_id: UUID) -> Alert:
    """Сигнал врача под блокировкой строки. Чужой или несуществующий — `AlertNotFoundError`."""
    alert = await session.scalar(
        locked(select(Alert).where(Alert.id == alert_id, Alert.doctor_id == doctor_id))
    )
    if alert is None:
        raise AlertNotFoundError
    return alert


async def acting(session: AsyncSession, doctor_id: UUID, alert_id: UUID) -> tuple[Alert, bool]:
    """Сигнал под блокировкой для ведущего врача или заместителя с действующим доступом
    (спека #56). Второе значение — действует заместитель. Иначе `AlertNotFoundError`."""
    alert = await session.scalar(
        locked(
            select(Alert).where(
                Alert.id == alert_id,
                or_(Alert.doctor_id == doctor_id, Alert.deputy_id == doctor_id),
            )
        )
    )
    if alert is None:
        raise AlertNotFoundError
    if alert.doctor_id == doctor_id:
        return alert, False
    granted = await patients.granted_alerts(
        session, doctor_id=doctor_id, patient_id=alert.patient_id
    )
    if alert.id not in granted:
        raise AlertNotFoundError
    return alert, True


async def publish(session: AsyncSession, doctor_id: UUID, alert_id: UUID, event_type: str) -> None:
    await realtime.publish(session, [doctor_id], event_type, {"alert_id": str(alert_id)})


async def publish_alert(session: AsyncSession, alert: Alert, event_type: str) -> None:
    """Событие сигнала ведущему врачу и заместителю, пока действует его доступ по сигналу.

    Действие, которое закрывает доступ, шлёт событие до закрытия: экран заместителя
    узнаёт о нём и убирает сигнал (#170).
    """
    to = [alert.doctor_id]
    if alert.deputy_id is not None and alert.id in await patients.granted_alerts(
        session, doctor_id=alert.deputy_id, patient_id=alert.patient_id
    ):
        to.append(alert.deputy_id)
    await realtime.publish(session, to, event_type, {"alert_id": str(alert.id)})


async def load(session: AsyncSession, alert_id: UUID, viewer_id: UUID | None = None) -> AlertOut:
    await session.flush()
    alert = await session.scalar(
        with_history().where(Alert.id == alert_id).execution_options(populate_existing=True)
    )
    if alert is None:
        raise AlertNotFoundError
    [out] = await to_out(session, [alert], viewer_id)
    return out


async def to_out(
    session: AsyncSession, alerts: Iterable[Alert], viewer_id: UUID | None = None
) -> list[AlertOut]:
    """Ответы с состоянием на сегодня (по Москве), ФИО врачей из истории и доступом
    заместителя. `viewer_id` — кто спрашивает: для `as_deputy`."""
    rows = list(alerts)
    ids = {user_id for a in rows for user_id in _people(a)}
    users = await identity.get_users(session, sorted(ids)) if ids else []
    actors = {u.id: u.full_name for u in users}
    grants = await patients.grants_of(session, [a.id for a in rows if a.deputy_id is not None])
    today = moscow_today()
    return [
        AlertOut.model_validate(a).model_copy(
            update={
                "state": effective_state(a.state, a.snoozed_until, today),
                "actors": {k: v for k, v in actors.items() if k in _people(a)},
                "deputy_access": grants.get(a.id),
                "as_deputy": viewer_id is not None and a.doctor_id != viewer_id,
            }
        )
        for a in rows
    ]


def _people(alert: Alert) -> set[UUID]:
    people = {
        alert.doctor_id,
        alert.deputy_id,
        alert.acknowledged_by,
        alert.resolved_by,
        *(x.actor_id for x in alert.actions),
        *(x.recipient_id for x in alert.actions),
    }
    return {p for p in people if p is not None}
