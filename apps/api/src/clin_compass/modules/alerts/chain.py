"""Шаги эскалации сигнала «Обязательно» на очереди (спека #56, раздел 4).

Сигнал стал «Обязательно» — `start` в той же транзакции отмечает `urgent_since` и ставит
задачу плана (шаг 0) без задержки. Сроки шагов знает worker из настроек: план ставит шаг 1
со сроком, каждый шаг — следующий. Шаг выполняется, только пока сигнал новый и
«Обязательно»; повтор шага ничего не меняет (ключ задачи и история `escalate`).
Каждый шаг — запись `escalate` в истории сигнала и событие без данных пациента.
"""

import json
from datetime import datetime, timedelta
from uuid import UUID

import structlog
from sqlalchemy import exists, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from clin_compass.core import queue, realtime
from clin_compass.modules.alerts import deputies, escalation
from clin_compass.modules.alerts.models import Action, Alert, Deputy
from clin_compass.modules.alerts.store import locked, publish_alert, with_history
from clin_compass.modules.identity import service as identity
from clin_compass.modules.identity.schemas import Role
from clin_compass.modules.patients import service as patients

ENTRYPOINT = "alerts.escalate"
PLAN = 0

log = structlog.get_logger()


def payload(alert_id: UUID, step: int) -> bytes:
    return json.dumps({"alert_id": str(alert_id), "step": step}).encode()


def parse(raw: bytes | None) -> tuple[UUID, int] | None:
    """Сигнал и шаг из задачи; испорченная задача — None."""
    try:
        data = json.loads(raw or b"")
        alert_id, step = UUID(str(data["alert_id"])), int(data["step"])
    except (ValueError, KeyError, TypeError):
        return None
    return (alert_id, step) if step in (PLAN, *escalation.STEPS) else None


async def start(session: AsyncSession, alert_id: UUID) -> None:
    """Сигнал стал «Обязательно»: отсчёт от этого момента и план шагов. Коммитит вызывающий.

    Отсчёт ставится один раз: понизить «Обязательно» нельзя, второго начала нет.
    """
    await session.execute(
        update(Alert)
        .where(Alert.id == alert_id, Alert.urgent_since.is_(None))
        .values(urgent_since=func.now())
    )
    await _enqueue(session, alert_id, PLAN, None)


async def run(
    session: AsyncSession, alert_id: UUID, step: int, minutes: tuple[int, int, int], now: datetime
) -> list[int]:
    """Выполнить шаг под блокировкой сигнала и поставить следующий. Коммитит вызывающий.

    Возвращает выполненные шаги: пусто — эскалация остановлена или шаг уже был.
    """
    alert = await session.scalar(locked(with_history().where(Alert.id == alert_id)))
    if alert is None or alert.urgent_since is None:
        return []
    if step == PLAN:
        await _schedule(session, alert, 1, minutes, now)
        return []
    if not escalation.escalates(alert.level, alert.state):
        return []
    done = {a.step for a in alert.actions if a.action == "escalate"}
    if step in done:
        return []
    assignment = None
    if step == 2:
        today = now.astimezone(escalation.MOSCOW).date()
        assignment = await deputies.on_day(session, alert.doctor_id, today)
    ran = [
        s
        for s in escalation.steps_to_run(step, deputy_found=assignment is not None)
        if s not in done
    ]
    for s in ran:
        await _step(session, alert, s, assignment, now)
    following = max(ran) + 1
    if following in escalation.STEPS:
        await _schedule(session, alert, following, minutes, now)
    return ran


async def notify_admins(session: AsyncSession, alert_id: UUID) -> None:
    """Сигнал в таблице эскалаций (любой шаг): его изменение — `escalation.updated` (#170)."""
    escalated = await session.scalar(
        select(exists().where(Action.alert_id == alert_id, Action.action == "escalate"))
    )
    if escalated:
        await _to_admins(session, alert_id, "escalation.updated")


async def _step(
    session: AsyncSession,
    alert: Alert,
    step: int,
    assignment: Deputy | None,
    now: datetime,
) -> None:
    recipient: UUID | None = None
    if step == 1:
        recipient = alert.doctor_id
    elif step == 2 and assignment is not None:
        recipient = alert.deputy_id = assignment.deputy_id
        await patients.grant_access(
            session,
            patient_id=alert.patient_id,
            doctor_id=assignment.deputy_id,
            alert_id=alert.id,
            expires_at=escalation.access_expires_at(now, assignment.ends_on),
        )
    session.add(
        Action(
            alert_id=alert.id, actor_id=None, action="escalate", step=step, recipient_id=recipient
        )
    )
    await session.flush()
    await publish_alert(session, alert, "alert.escalated")
    # Новой эскалацией с плашкой администратор считает шаг 3; шаги 1 и 2 обновляют таблицу.
    await _to_admins(session, alert.id, "escalation.created" if step == 3 else "escalation.updated")
    log.info("alert.escalated", alert_id=str(alert.id), step=step, deputy=recipient is not None)


async def _to_admins(session: AsyncSession, alert_id: UUID, event_type: str) -> None:
    admins = [u.id for u in await identity.list_users(session, Role.ADMIN)]
    if admins:
        await realtime.publish(session, admins, event_type, {"alert_id": str(alert_id)})


async def _schedule(
    session: AsyncSession, alert: Alert, step: int, minutes: tuple[int, int, int], now: datetime
) -> None:
    assert alert.urgent_since is not None
    wait = escalation.due_at(alert.urgent_since, step, minutes) - now
    await _enqueue(session, alert.id, step, max(wait, timedelta(0)))


async def _enqueue(
    session: AsyncSession, alert_id: UUID, step: int, wait: timedelta | None
) -> None:
    await queue.enqueue(
        session,
        ENTRYPOINT,
        payload(alert_id, step),
        dedupe_key=f"alert:{alert_id}:step:{step}",
        execute_after=wait,
    )
