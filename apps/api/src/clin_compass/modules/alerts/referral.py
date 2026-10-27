"""Направление к психиатру (спека #35): предложение по сигналу маршрутизации и решение врача.

Сигнал маршрутизации создаёт предложение `alerts.referral`; открытое предложение одно
на пациента. Решение принимает одна ручка: подтвердить, отклонить с причиной или отложить.
Подтверждение и отказ решают предложение и закрывают все нерешённые сигналы
маршрутизации пациента в одной транзакции. Сама система никого не направляет.
"""

from collections.abc import Sequence
from uuid import UUID

from clin_compass_knowledge.schemas.signal_decisions import DeclineReason, SignalDecisions
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from clin_compass.modules.alerts import lifecycle
from clin_compass.modules.alerts.handling import apply_close, apply_snooze
from clin_compass.modules.alerts.lifecycle import ConflictError, InvalidError
from clin_compass.modules.alerts.models import Alert
from clin_compass.modules.alerts.schemas import AlertOut, ReferralIn, ReferralOut
from clin_compass.modules.alerts.store import to_out, with_history
from clin_compass.modules.proposals import service as proposals
from clin_compass.modules.proposals.schemas import ProposalOut

KIND = "alerts.referral"
# Запись о подтверждении направления, а не клинический вариант выбора: основание — источники
# правила маршрутизации у самого сигнала.
REFERRED_ID = "referred"
REFERRED_TEXT = "Направлен к психиатру"
CHANGED = "Сигналы изменились, проверьте лист"


def check_seen(open_ids: Sequence[UUID], seen: Sequence[UUID]) -> None:
    """Решать можно только то, что врач видел в листе.

    Raises:
        ConflictError: Нерешённых сигналов нет или набор не совпадает с показанным.
    """
    if not open_ids:
        raise ConflictError(lifecycle.ALREADY_RESOLVED)
    if set(open_ids) != set(seen):
        raise ConflictError(CHANGED)


def check_decline(
    decisions: SignalDecisions | None, reason_id: str | None, comment: str | None
) -> DeclineReason:
    """Причина из `referral_decline` и непустой комментарий (данные медика #93).

    Raises:
        ConflictError: Данных о причинах нет.
        InvalidError: Причины нет в списке или нет комментария.
    """
    if decisions is None:
        raise ConflictError(lifecycle.NO_DATA)
    reason = next((r for r in decisions.referral_decline.reasons if r.id == reason_id), None)
    if reason is None:
        raise InvalidError("Выберите причину из списка")
    if not (comment or "").strip():
        raise InvalidError("Напишите комментарий: причина отказа сохраняется в карточке")
    return reason


def decline_comment(reason_text: str, comment: str | None) -> str:
    """Комментарий решения по предложению: причина и слова врача."""
    return f"{reason_text}. {(comment or '').strip()}"


async def propose_for(session: AsyncSession, alert: AlertOut) -> None:
    """Предложение направления по сигналу маршрутизации, если открытого у пациента нет.

    Вызывающий держит `lock` пациента, взятый до блокировок строк сигналов: порядок
    блокировок тот же, что в `decide`.
    """
    if not await proposals.list_open(session, patient_id=alert.patient_id, kind=KIND):
        await _propose(session, alert.patient_id, alert.id, alert.rule_id, alert.level, alert.title)


async def decide(
    session: AsyncSession,
    registry: proposals.ApplierRegistry,
    decisions: SignalDecisions | None,
    *,
    doctor_id: UUID,
    patient_id: UUID,
    body: ReferralIn,
) -> ReferralOut:
    """Решение врача по направлению. Коммитит вызывающий. Ошибки — `ConflictError`,
    `InvalidError` и ошибки `proposals`."""
    await lock(session, patient_id)
    rows = list(
        (
            await session.scalars(
                select(Alert)
                .where(
                    Alert.patient_id == patient_id,
                    Alert.kind == "routing",
                    Alert.state != "resolved",
                )
                .order_by(Alert.created_at)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).all()
    )
    check_seen([a.id for a in rows], body.alert_ids)
    if body.decision == "snooze":
        if body.until is None:
            raise InvalidError("Выберите дату, до которой отложить")
        today = lifecycle.moscow_today()
        for alert in rows:
            lifecycle.check_snooze(alert.level, alert.state, body.until, today, decisions)
        for alert in rows:
            await apply_snooze(session, alert, doctor_id, body.until)
        return ReferralOut(proposal=None, alerts=await _reload(session, rows))

    reason = (
        check_decline(decisions, body.reason_id, body.comment)
        if body.decision == "decline"
        else None
    )
    proposal = await _open_proposal(session, rows[0])
    if reason is None:
        decided = await proposals.decide(
            session,
            registry,
            doctor_id=doctor_id,
            proposal_id=proposal.id,
            confirm=True,
            comment=(body.comment or "").strip() or None,
        )
        for alert in rows:
            await apply_close(session, alert, doctor_id, REFERRED_ID, REFERRED_TEXT, body.comment)
    else:
        decided = await proposals.decide(
            session,
            registry,
            doctor_id=doctor_id,
            proposal_id=proposal.id,
            confirm=False,
            comment=decline_comment(reason.text, body.comment),
        )
        for alert in rows:
            await apply_close(session, alert, doctor_id, reason.id, reason.text, body.comment)
    return ReferralOut(proposal=decided, alerts=await _reload(session, rows))


def register_appliers(registry: proposals.ApplierRegistry) -> None:
    """Направление решается ручкой `alerts`: applier ничего не делает сверх `decide`."""

    async def apply(_session: AsyncSession, _proposal: ProposalOut) -> None:
        return None

    registry.register(KIND, apply, decided_in_module=True)


async def _open_proposal(session: AsyncSession, first: Alert) -> ProposalOut:
    """Открытое предложение пациента. Сигнал до #35 его не имеет: создаётся здесь."""
    found = await proposals.list_open(session, patient_id=first.patient_id, kind=KIND)
    if found:
        return found[0]
    return await _propose(
        session, first.patient_id, first.id, first.rule_id, first.level, first.title
    )


async def _propose(
    session: AsyncSession,
    patient_id: UUID,
    alert_id: UUID,
    rule_id: str | None,
    level: str,
    title: str,
) -> ProposalOut:
    return await proposals.create_proposal(
        session,
        patient_id=patient_id,
        kind=KIND,
        payload={"alert_id": str(alert_id), "rule_id": rule_id, "level": level, "title": title},
        origin_type="rule",
        origin_ref=str(alert_id),
    )


async def _reload(session: AsyncSession, rows: list[Alert]) -> list[AlertOut]:
    await session.flush()
    fresh = await session.scalars(
        with_history()
        .where(Alert.id.in_([a.id for a in rows]))
        .order_by(Alert.created_at)
        .execution_options(populate_existing=True)
    )
    return await to_out(session, fresh)


async def lock(session: AsyncSession, patient_id: UUID) -> None:
    """Предложение и решение по направлению одного пациента — по очереди: уникального
    индекса у предложений нет. Берётся раньше блокировок строк сигналов: и в
    `check_results`, и в `decide` порядок одинаковый, взаимной блокировки нет."""
    key = func.hashtextextended(f"alerts.referral:{patient_id}", 0)
    await session.execute(select(func.pg_advisory_xact_lock(key)))
