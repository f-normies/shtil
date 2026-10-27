"""Публичный интерфейс модуля alerts: сигналы по правилам и из AI-контура, доставка, действия
врача (взять в работу, отложить, «не удалось связаться», решение — `handling.py`, спека #95).

Сигнал адресован ведущему врачу пациента. Событие `alert.created` или `alert.raised` уходит
по WebSocket при коммите транзакции вызывающего: откат не рассылает ничего. В событии только
id сигнала, данные кабинет врача читает по REST.

Сигнал, который стал «Обязательно» при создании или подъёме уровня, эскалируется (спека #56,
`chain.py`): отсчёт и план шагов ставятся в той же транзакции.

Модуль может создать сигнал и поднять его уровень. Понизить уровень или удалить сигнал нельзя:
таких функций нет (спека #12, «LLM может только поднять сигнал»). Решённый сигнал не меняется:
новое срабатывание по тому же правилу создаёт новый сигнал.
Удалить сигнал или изменить историю срабатываний не даёт и БД: это запрещают её триггеры.

Объединение повторов рассчитано на уровень изоляции READ COMMITTED (по умолчанию в Postgres):
после конфликта вставки повторный поиск видит сигнал, созданный другой транзакцией.
Время доставки и подтверждения берётся в БД, как и время создания: задержка считается
по одним часам.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast
from uuid import UUID

import structlog
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from clin_compass.core import metrics
from clin_compass.modules.alerts import chain, referral
from clin_compass.modules.alerts.handling import (
    acknowledge,
    decisions_view,
    list_alerts,
    no_contact,
    resolve,
    snooze,
    thresholds,
)
from clin_compass.modules.alerts.help import alert_raised_by
from clin_compass.modules.alerts.lifecycle import ConflictError, InvalidError, after_raise
from clin_compass.modules.alerts.models import OPEN_RULE_ALERT, Action, Alert, Trigger
from clin_compass.modules.alerts.referral import decide as decide_referral
from clin_compass.modules.alerts.referral import register_appliers
from clin_compass.modules.alerts.rules import Firing, RuleBook
from clin_compass.modules.alerts.schemas import AlertOut, DeliveryChannel, SourceSnapshot
from clin_compass.modules.alerts.store import (
    AlertNotFoundError,
    load,
    locked,
    owned,
    publish,
    publish_alert,
    to_out,
    with_history,
)
from clin_compass.modules.patients import service as patients

__all__ = [
    "AlertNotFoundError",
    "ConflictError",
    "InvalidError",
    "PatientNotFoundError",
    "acknowledge",
    # Признак сигнала по отправке шкалы (спека #57) читает assessments.
    "alert_raised_by",
    "check_results",
    "decide_referral",
    "decisions_view",
    "list_alerts",
    "list_for_patient",
    "list_open_for_doctor",
    "mark_delivered",
    "no_contact",
    "raise_safety",
    "raise_signal",
    "register_appliers",
    "resolve",
    "snooze",
    "thresholds",
]

log = structlog.get_logger()

# Сколько раз пробуем записать срабатывание, если сигнал одновременно создали в другой
# транзакции. Второй попытки хватает: после конфликта открытый сигнал уже виден.
_RECORD_ATTEMPTS = 3


class PatientNotFoundError(Exception):
    """Пациента нет: сигнал некому адресовать."""


async def check_results(
    session: AsyncSession,
    rules: RuleBook,
    *,
    patient_id: UUID,
    submitted_scale: str,
    results: dict[str, dict[str, float]],
    origin_ref: str,
) -> list[AlertOut]:
    """Исполнить правила после отправки шкалы. Коммитит вызывающий.

    `submitted_scale` — шкала, которую пациент только что отправил: срабатывают правила,
    условие которых истинно благодаря ей (`RuleBook.fire`). `results` — последние результаты
    пациента по каждой шкале (`{scale_id: {item_id или score_id: число}}`): так правило
    по двум шкалам срабатывает, когда приходит вторая. `origin_ref` — непустая ссылка
    на отправку шкалы. Пациента нет — `PatientNotFoundError`.
    """
    firings = rules.fire(results, submitted_scale)
    if not firings:
        return []
    doctor_id = await _lead_doctor(session, patient_id)
    if any(f.rule.kind == "routing" for f in firings):
        # Блокировка направления — до блокировок строк сигналов, как в решении врача (#35).
        await referral.lock(session, patient_id)
    recorded = [
        await _record_firing(
            session,
            _RuleSignal.of(f),
            patient_id=patient_id,
            doctor_id=doctor_id,
            origin_ref=origin_ref,
        )
        for f in firings
    ]
    # Сигнал маршрутизации — предложение направления к психиатру (спека #35).
    for alert in recorded:
        if alert.kind == "routing" and alert.state != "resolved":
            await referral.propose_for(session, alert)
    return recorded


async def raise_safety(
    session: AsyncSession,
    *,
    patient_id: UUID,
    rule_id: str,
    rule_version: int,
    level: str,
    title: str,
    doctor_actions: list[str],
    sources: list[SourceSnapshot],
    evidence: dict[str, Any],
    origin_ref: str,
) -> AlertOut:
    """Сигнал безопасности терапии (спека #29): противопоказание, предосторожность или
    нежелательная реакция. Коммитит вызывающий.

    Тот же путь, что у правил шкал: один открытый сигнал на `rule_id` у пациента, повтор
    дописывается срабатыванием и может только поднять уровень.
    """
    if not sources or not doctor_actions:
        raise ValueError("safety signal needs sources and doctor_actions")
    doctor_id = await _lead_doctor(session, patient_id)
    signal = _RuleSignal(
        rule_id, rule_version, "safety", level, title, doctor_actions, sources, evidence
    )
    return await _record_firing(
        session, signal, patient_id=patient_id, doctor_id=doctor_id, origin_ref=origin_ref
    )


async def raise_signal(
    session: AsyncSession,
    *,
    patient_id: UUID,
    title: str,
    doctor_actions: list[str],
    evidence: dict[str, Any],
    ai_run_id: UUID,
    alert_id: UUID | None = None,
) -> AlertOut:
    """Сигнал из AI-контура. Коммитит вызывающий.

    Без `alert_id` — новый сигнал уровня `mandatory`: порогов, по которым можно выбрать
    «желательно», у AI нет. С `alert_id` — поднять открытый сигнал пациента до `mandatory`
    и записать срабатывание. Уже `mandatory` сигнал уровень не меняет, срабатывание всё равно
    пишется. Если врач уже решил этот сигнал, создаётся новый: сигнал AI не теряется.
    `ai_run_id` — id записи `ai_runs`. Пустые `title` или `doctor_actions` — `ValueError`.
    Пациента нет — `PatientNotFoundError`.
    """
    if not title.strip() or not doctor_actions or not all(a.strip() for a in doctor_actions):
        raise ValueError("AI signal needs title and doctor_actions")
    origin_ref = str(ai_run_id)
    if alert_id is not None:
        existing = await session.scalar(
            locked(select(Alert).where(Alert.id == alert_id, Alert.patient_id == patient_id))
        )
        if existing is None:
            raise AlertNotFoundError
        if existing.state != "resolved":
            return await _append_firing(session, existing, origin_ref, None, "mandatory", evidence)

    doctor_id = await _lead_doctor(session, patient_id)
    alert = Alert(
        patient_id=patient_id,
        doctor_id=doctor_id,
        origin_type="ai_run",
        kind="alert",
        level="mandatory",
        title=title,
        doctor_actions=doctor_actions,
        sources=[],
    )
    session.add(alert)
    await session.flush()
    _add_trigger(session, alert.id, origin_ref, None, "mandatory", evidence)
    await chain.start(session, alert.id)
    await publish(session, doctor_id, alert.id, "alert.created")
    return await load(session, alert.id)


async def mark_delivered(
    session: AsyncSession, *, doctor_id: UUID, alert_id: UUID, via: DeliveryChannel
) -> None:
    """Кабинет врача получил сигнал. Повторный вызов ничего не меняет."""
    alert = await owned(session, doctor_id, alert_id)
    if alert.delivered_at is not None:
        return
    alert.delivered_at = func.clock_timestamp()
    alert.delivered_via = via
    await session.flush()
    await session.refresh(alert, ["delivered_at", "created_at"])
    # Время доставки только что поставила БД: после refresh оно заполнено.
    delivered_at = cast(datetime, alert.delivered_at)
    seconds = (delivered_at - alert.created_at).total_seconds()
    origin = "rule" if alert.origin_type == "rule" else "ai"
    metrics.ALERT_DELIVERY_SECONDS.labels(origin=origin, via=via).observe(seconds)
    log.info("alert.delivered", alert_id=str(alert.id), via=via, latency_ms=round(seconds * 1000))


async def list_open_for_doctor(session: AsyncSession, *, doctor_id: UUID) -> list[AlertOut]:
    """Нерешённые сигналы врача: кабинет дочитывает их после подключения."""
    return await list_alerts(session, doctor_id=doctor_id, status="open")


async def list_for_patient(
    session: AsyncSession, *, patient_id: UUID, viewer_id: UUID | None = None, deputy: bool = False
) -> list[AlertOut]:
    """Сигналы пациента с историей. Право доступа проверяет вызывающий (`owned_patient`).

    `deputy` — спрашивает заместитель: только сигналы, по которым у него доступ (спека #56).
    """
    query = with_history().where(Alert.patient_id == patient_id)
    if deputy and viewer_id is not None:
        granted = await patients.granted_alerts(session, doctor_id=viewer_id, patient_id=patient_id)
        query = query.where(Alert.id.in_(granted))
    rows = await session.scalars(query.order_by(Alert.created_at))
    return await to_out(session, rows, viewer_id)


@dataclass(frozen=True)
class _RuleSignal:
    """Что записать по сработавшему правилу: правило шкал или правило безопасности."""

    id: str
    version: int
    kind: str
    level: str
    title: str
    doctor_actions: list[str]
    sources: list[SourceSnapshot]
    evidence: dict[str, Any]

    @classmethod
    def of(cls, firing: Firing) -> "_RuleSignal":
        rule = firing.rule
        return cls(
            rule.id,
            rule.version,
            rule.kind,
            rule.level,
            rule.title,
            list(rule.doctor_actions),
            firing.sources,
            dict(firing.evidence),
        )


async def _record_firing(
    session: AsyncSession,
    rule: _RuleSignal,
    *,
    patient_id: UUID,
    doctor_id: UUID,
    origin_ref: str,
) -> AlertOut:
    for _ in range(_RECORD_ATTEMPTS):
        existing = await session.scalar(
            locked(
                select(Alert).where(
                    Alert.patient_id == patient_id,
                    Alert.rule_id == rule.id,
                    Alert.state != "resolved",
                )
            )
        )
        if existing is not None:
            return await _append_firing(
                session, existing, origin_ref, rule.version, rule.level, rule.evidence
            )

        # Вставка не ждёт блокировки строки, которой ещё нет: одновременный сигнал по тому
        # же правилу ловит уникальный индекс, и мы повторяем поиск открытого сигнала.
        created_id = await session.scalar(
            insert(Alert)
            .values(
                patient_id=patient_id,
                doctor_id=doctor_id,
                origin_type="rule",
                rule_id=rule.id,
                rule_version=rule.version,
                kind=rule.kind,
                level=rule.level,
                title=rule.title,
                doctor_actions=list(rule.doctor_actions),
                sources=[s.model_dump() for s in rule.sources],
            )
            .on_conflict_do_nothing(
                index_elements=["patient_id", "rule_id"], index_where=OPEN_RULE_ALERT
            )
            .returning(Alert.id)
        )
        if created_id is not None:
            _add_trigger(session, created_id, origin_ref, rule.version, rule.level, rule.evidence)
            if rule.level == "mandatory":
                await chain.start(session, created_id)
            await publish(session, doctor_id, created_id, "alert.created")
            return await load(session, created_id)
    raise RuntimeError(f"Could not record firing of {rule.id} for patient {patient_id}")


async def _append_firing(
    session: AsyncSession,
    alert: Alert,
    origin_ref: str,
    rule_version: int | None,
    level: str,
    evidence: dict[str, Any],
) -> AlertOut:
    """Дописать срабатывание к нерешённому сигналу.

    Уровень поднялся — `alert.raised`, иначе `alert.updated`: кабинет врача обновляет
    сигнал без перезагрузки (#13). Подъём уровня снимает отсрочку (спека #95): сигнал
    снова новый, в истории — действие системы `unsnooze`.
    """
    raised = _raise_level(alert, level)
    if raised and (state := after_raise(alert.state)) is not None:
        alert.state, alert.snoozed_until = state, None
        session.add(Action(alert_id=alert.id, actor_id=None, action="unsnooze"))
    alert.occurrences += 1
    alert.last_triggered_at = func.now()
    _add_trigger(session, alert.id, origin_ref, rule_version, level, evidence)
    if raised:
        # Отсчёт эскалации — от подъёма до «Обязательно» (спека #56).
        await session.flush()
        await chain.start(session, alert.id)
    event_type = "alert.raised" if raised else "alert.updated"
    await publish_alert(session, alert, event_type)
    return await load(session, alert.id)


def _raise_level(alert: Alert, level: str) -> bool:
    """Поднять уровень, если требуется. Понижения нет."""
    if level == "mandatory" and alert.level == "desirable":
        alert.level = "mandatory"
        return True
    return False


def _add_trigger(
    session: AsyncSession,
    alert_id: UUID,
    origin_ref: str,
    rule_version: int | None,
    level: str,
    evidence: dict[str, Any],
) -> None:
    session.add(
        Trigger(
            alert_id=alert_id,
            origin_ref=origin_ref,
            rule_version=rule_version,
            level=level,
            evidence=evidence,
        )
    )


async def _lead_doctor(session: AsyncSession, patient_id: UUID) -> UUID:
    doctor_id = await patients.lead_doctor_of(session, patient_id)
    if doctor_id is None:
        raise PatientNotFoundError
    return doctor_id
