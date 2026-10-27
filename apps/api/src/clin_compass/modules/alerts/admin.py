"""Разбор эскалаций администратором (спека #56, разделы 2 и 5).

Администратор видит, кому и когда передан сигнал и кто открыл карту по замещению. Данных
пациента в ответе нет: ни ФИО, ни заголовка, ни уровня. Коммитит вызывающий.
"""

from typing import Literal
from uuid import UUID

from sqlalchemy import Exists, exists, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from clin_compass.modules.alerts import chain
from clin_compass.modules.alerts.models import Action, Alert, Review
from clin_compass.modules.alerts.schemas import EscalationOut, EscalationStepOut
from clin_compass.modules.identity import service as identity
from clin_compass.modules.patients import service as patients


class EscalationNotFoundError(Exception):
    """Сигнала нет или он не эскалировался."""


def _escalated() -> Exists:
    return exists().where(Action.alert_id == Alert.id, Action.action == "escalate")


async def list_escalations(
    session: AsyncSession, status: Literal["open", "all"]
) -> list[EscalationOut]:
    """Эскалации, новые первыми. `open` — ещё не разобранные."""
    query = select(Alert).options(selectinload(Alert.actions)).where(_escalated())
    if status == "open":
        query = query.where(~exists().where(Review.alert_id == Alert.id))
    alerts = list(await session.scalars(query.order_by(Alert.urgent_since.desc(), Alert.id)))
    reviews = {
        r.alert_id: r
        for r in await session.scalars(
            select(Review).where(Review.alert_id.in_([a.id for a in alerts]))
        )
    }
    grants = await patients.grants_of(session, [a.id for a in alerts])
    people = {
        p
        for a in alerts
        for p in (
            a.doctor_id,
            a.deputy_id,
            a.acknowledged_by,
            *(x.actor_id for x in a.actions),
            *(r.reviewed_by for r in [reviews.get(a.id)] if r),
        )
        if p is not None
    }
    names = {u.id: u.full_name for u in await identity.get_users(session, sorted(people))}
    result = []
    for a in alerts:
        steps = [
            EscalationStepOut(step=x.step, at=x.at, recipient_id=x.recipient_id)
            for x in a.actions
            if x.action == "escalate" and x.step is not None
        ]
        review = reviews.get(a.id)
        mine = {a.doctor_id, a.deputy_id, a.acknowledged_by, review.reviewed_by if review else None}
        result.append(
            EscalationOut(
                alert_id=a.id,
                number=str(a.id)[:8],
                urgent_since=a.urgent_since,
                doctor_id=a.doctor_id,
                deputy_id=a.deputy_id,
                step=max(s.step for s in steps),
                steps=steps,
                acknowledged_at=a.acknowledged_at,
                acknowledged_by=a.acknowledged_by,
                resolved_at=a.resolved_at,
                access=grants.get(a.id),
                reviewed_at=review.reviewed_at if review else None,
                reviewed_by=review.reviewed_by if review else None,
                names={k: v for k, v in names.items() if k in mine},
            )
        )
    return result


async def review(session: AsyncSession, *, admin_id: UUID, alert_id: UUID) -> None:
    """Отметить эскалацию разобранной. Повтор ничего не меняет."""
    escalated = await session.scalar(
        select(exists().where(Action.alert_id == alert_id, Action.action == "escalate"))
    )
    if not escalated:
        raise EscalationNotFoundError
    await session.execute(
        insert(Review)
        .values(alert_id=alert_id, reviewed_by=admin_id)
        .on_conflict_do_nothing(index_elements=["alert_id"])
    )
    await chain.notify_admins(session, alert_id)
