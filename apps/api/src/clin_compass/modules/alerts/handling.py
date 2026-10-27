"""Действия врача по сигналу (спека #95): взять в работу, отложить, «не удалось связаться»,
решение; списки сигналов, варианты решений и пороги правил.

Каждое действие — запись в `alerts.actions` и событие `alert.updated` ведущему врачу и
заместителю. Переходы проверяет `lifecycle.py`. Коммитит вызывающий.

Заместитель с действующим доступом (спека #56) берёт сигнал в работу, отмечает «Не удалось
связаться» и решает его; отложить не может. Любое действие ведущего врача закрывает доступ
заместителя (`lead_took`), решение — тоже (`resolved`).
"""

from datetime import date
from typing import Literal
from uuid import UUID

from clin_compass_knowledge.schemas.rule import Rule
from clin_compass_knowledge.schemas.signal_decisions import SignalDecisions
from sqlalchemy import func, or_
from sqlalchemy.ext.asyncio import AsyncSession

from clin_compass.modules.alerts import chain, lifecycle
from clin_compass.modules.alerts.lifecycle import moscow_today
from clin_compass.modules.alerts.models import Action, Alert
from clin_compass.modules.alerts.schemas import (
    AlertOut,
    DecisionOptionOut,
    DecisionsOut,
    ReasonOut,
    SnoozeOptionsOut,
    ThresholdOut,
)
from clin_compass.modules.alerts.store import (
    acting,
    load,
    owned,
    publish_alert,
    to_out,
    with_history,
)
from clin_compass.modules.patients import service as patients


async def acknowledge(session: AsyncSession, *, doctor_id: UUID, alert_id: UUID) -> AlertOut:
    """Врач занялся сигналом: новый или отложенный → в работе. Повтор ничего не меняет."""
    alert, as_deputy = await acting(session, doctor_id, alert_id)
    state = lifecycle.after_acknowledge(alert.state)
    if state is not None:
        _take(alert, doctor_id)
        _record(session, alert, doctor_id, "acknowledge")
        await _changed(session, alert)
    if not as_deputy:
        await patients.end_access(session, alert_id=alert.id, reason="lead_took")
    return await load(session, alert.id, doctor_id)


async def snooze(
    session: AsyncSession,
    decisions: SignalDecisions | None,
    *,
    doctor_id: UUID,
    alert_id: UUID,
    until: date,
) -> AlertOut:
    """Отложить «Желательно» до даты. Ошибки — `lifecycle.ConflictError` и `InvalidError`."""
    alert = await owned(session, doctor_id, alert_id)
    lifecycle.check_snooze(alert.level, alert.state, until, moscow_today(), decisions)
    await apply_snooze(session, alert, doctor_id, until)
    return await load(session, alert.id, doctor_id)


async def no_contact(session: AsyncSession, *, doctor_id: UUID, alert_id: UUID) -> AlertOut:
    """«Не удалось связаться»: запись в истории, сигнал «в работе»."""
    alert, as_deputy = await acting(session, doctor_id, alert_id)
    lifecycle.after_no_contact(alert.kind, alert.state)
    _take(alert, doctor_id)
    _record(session, alert, doctor_id, "no_contact")
    await _changed(session, alert)
    if not as_deputy:
        await patients.end_access(session, alert_id=alert.id, reason="lead_took")
    return await load(session, alert.id, doctor_id)


async def resolve(
    session: AsyncSession,
    decisions: SignalDecisions | None,
    *,
    doctor_id: UUID,
    alert_id: UUID,
    decision_id: str,
    reason: str | None,
) -> AlertOut:
    """Решение по сигналу: вариант из данных и причина. Решить можно без взятия в работу.

    Решение закрывает доступ заместителя: данные пациента ему больше не нужны.
    """
    alert, _ = await acting(session, doctor_id, alert_id)
    if alert.state == "resolved":
        raise lifecycle.ConflictError(lifecycle.ALREADY_RESOLVED)
    full = await load(session, alert.id)
    options = lifecycle.decision_options(alert.kind, _safety_kind(full), decisions)
    chosen = lifecycle.check_resolve(alert.level, alert.state, options, decision_id, reason)
    await apply_close(session, alert, doctor_id, chosen.id, chosen.text, reason)
    return await load(session, alert.id, doctor_id)


async def apply_snooze(session: AsyncSession, alert: Alert, doctor_id: UUID, until: date) -> None:
    """Отложить заблокированный сигнал: проверки — у вызывающего (`lifecycle.check_snooze`)."""
    alert.state, alert.snoozed_until = "snoozed", until
    _record(session, alert, doctor_id, "snooze", snoozed_until=until)
    await _changed(session, alert)


async def apply_close(
    session: AsyncSession,
    alert: Alert,
    doctor_id: UUID,
    decision_id: str,
    decision_text: str,
    reason: str | None,
) -> None:
    """Решить заблокированный сигнал: вариант и причина уже проверены вызывающим.

    Решение без взятия в работу тоже значит, что врач занялся сигналом: ставится
    `acknowledged_at`, если его не было.
    """
    if alert.acknowledged_at is None:
        alert.acknowledged_at = func.clock_timestamp()
        alert.acknowledged_by = doctor_id
    alert.state, alert.snoozed_until = "resolved", None
    alert.resolved_at, alert.resolved_by = func.clock_timestamp(), doctor_id
    _record(
        session,
        alert,
        doctor_id,
        "resolve",
        decision_id=decision_id,
        decision_text=decision_text,
        reason=(reason or "").strip() or None,
    )
    await _changed(session, alert)
    await patients.end_access(session, alert_id=alert.id, reason="resolved")


async def list_alerts(
    session: AsyncSession,
    *,
    doctor_id: UUID,
    status: Literal["open", "resolved"],
    patient_id: UUID | None = None,
) -> list[AlertOut]:
    """Сигналы врача: открытые (новые, в работе, отложенные) или решённые, старые первыми.

    С ними — сигналы, по которым у врача действующий доступ заместителя (спека #56).
    """
    granted = await patients.granted_alerts(session, doctor_id=doctor_id)
    query = with_history().where(or_(Alert.doctor_id == doctor_id, Alert.id.in_(granted)))
    query = query.where(
        Alert.state == "resolved" if status == "resolved" else Alert.state != "resolved"
    )
    if patient_id is not None:
        query = query.where(Alert.patient_id == patient_id)
    rows = await session.scalars(query.order_by(Alert.created_at))
    return await to_out(session, rows, doctor_id)


def decisions_view(decisions: SignalDecisions | None) -> DecisionsOut:
    """Варианты решений и пределы отсрочки для формы врача. Без данных — пустые списки."""
    if decisions is None:
        return DecisionsOut(
            safety=[], no_contact=None, therapy=[], referral_decline=[], snooze=None
        )
    return DecisionsOut(
        safety=[
            DecisionOptionOut(id=d.id, text=d.text, reason_hint=d.reason_hint)
            for d in decisions.safety.decisions
        ],
        no_contact=decisions.safety.no_contact.text,
        therapy=[
            DecisionOptionOut(
                id=d.id, text=d.text, reason_hint=d.reason_hint, applies_to=list(d.applies_to)
            )
            for d in decisions.therapy.decisions
        ],
        referral_decline=[
            ReasonOut(id=r.id, text=r.text) for r in decisions.referral_decline.reasons
        ],
        snooze=SnoozeOptionsOut(
            max_days=decisions.snooze.max_days,
            presets_days=list(decisions.snooze.presets_days),
        ),
    )


def thresholds(rules: list[Rule], scale: str) -> list[ThresholdOut]:
    """Пороги правил по шкале: каждое сравнение суммы или пункта этой шкалы."""
    found: list[ThresholdOut] = []
    for rule in rules:
        for leaf in rule.when.leaves():
            if leaf.scale != scale or leaf.op is None or leaf.value is None:
                continue
            found.append(
                ThresholdOut(
                    rule_id=rule.id,
                    kind=rule.kind,
                    level=rule.level,
                    item=leaf.item,
                    score=leaf.score,
                    op=leaf.op,
                    value=leaf.value,
                )
            )
    return found


async def _changed(session: AsyncSession, alert: Alert) -> None:
    """Событие врачам сигнала; если сигнал дошёл до администраторов — и им."""
    await publish_alert(session, alert, "alert.updated")
    if alert.urgent_since is not None:
        await chain.notify_admins(session, alert.id)


def _take(alert: Alert, doctor_id: UUID) -> None:
    """В работу: отсрочка снимается, первое взятие запоминается."""
    alert.state, alert.snoozed_until = "in_work", None
    if alert.acknowledged_at is None:
        alert.acknowledged_at = func.clock_timestamp()
        alert.acknowledged_by = doctor_id


def _record(
    session: AsyncSession,
    alert: Alert,
    doctor_id: UUID,
    action: str,
    *,
    decision_id: str | None = None,
    decision_text: str | None = None,
    reason: str | None = None,
    snoozed_until: date | None = None,
) -> None:
    session.add(
        Action(
            alert_id=alert.id,
            actor_id=doctor_id,
            action=action,
            decision_id=decision_id,
            decision_text=decision_text,
            reason=reason,
            snoozed_until=snoozed_until,
        )
    )


def _safety_kind(alert: AlertOut) -> str | None:
    """Вид сигнала безопасности терапии из первого срабатывания (`evidence.safety_kind`)."""
    kind = alert.triggers[0].evidence.get("safety_kind") if alert.triggers else None
    return kind if isinstance(kind, str) else None
