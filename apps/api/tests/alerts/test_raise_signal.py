"""Сигналы из AI-контура (спека #12, раздел 3): AI создаёт сигнал или поднимает уровень.

Снять или понизить сигнал AI не может. Сигнал AI не должен потеряться: если связанный
сигнал уже подтверждён, создаётся новый.
"""

from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.modules.alerts import service as alerts
from clin_compass.modules.alerts.models import Alert
from clin_compass.modules.alerts.rules import RuleBook
from clin_compass.modules.alerts.schemas import AlertOut
from tests.alerts.conftest import (
    HIGH_MOOD,
    SELF_HARM,
    Care,
    Notifications,
    acknowledge,
    check,
    event,
    make_care,
    patient_alerts,
    resolve,
)

AI_RUN = UUID("01890000-0000-7000-8000-000000000001")


async def raise_signal(
    maker: async_sessionmaker[AsyncSession], care: Care, alert_id: UUID | None = None
) -> AlertOut:
    async with maker() as session:
        alert = await alerts.raise_signal(
            session,
            patient_id=care.patient.id,
            title="Признаки риска в дневнике",
            doctor_actions=["Связаться с пациентом"],
            evidence={"quote": "не вижу смысла"},
            ai_run_id=AI_RUN,
            alert_id=alert_id,
        )
        await session.commit()
        return alert


async def test_ai_signal_is_mandatory_alert_without_documents(
    sessionmaker: async_sessionmaker[AsyncSession], care: Care, notifications: Notifications
) -> None:
    alert = await raise_signal(sessionmaker, care)

    assert (alert.origin_type, alert.kind, alert.level) == ("ai_run", "alert", "mandatory")
    assert alert.rule_id is None
    assert alert.sources == []
    assert [(t.origin_ref, t.evidence) for t in alert.triggers] == [
        (str(AI_RUN), {"quote": "не вижу смысла"})
    ]
    assert await notifications.wait_for(1) == [event("alert.created", alert, care.doctor.id)]


async def test_ai_raises_desirable_signal_to_mandatory(
    sessionmaker: async_sessionmaker[AsyncSession],
    rules: RuleBook,
    care: Care,
    notifications: Notifications,
) -> None:
    [alert] = await check(sessionmaker, rules, care.patient.id, HIGH_MOOD)

    raised = await raise_signal(sessionmaker, care, alert.id)

    assert raised.id == alert.id
    assert raised.level == "mandatory"
    assert [t.origin_ref for t in raised.triggers] == ["submission:1", str(AI_RUN)]
    assert await notifications.wait_for(2) == [
        event("alert.created", alert, care.doctor.id),
        event("alert.raised", alert, care.doctor.id),
    ]


async def test_ai_signal_on_mandatory_alert_is_recorded_with_update_event(
    sessionmaker: async_sessionmaker[AsyncSession],
    rules: RuleBook,
    care: Care,
    notifications: Notifications,
) -> None:
    [alert] = await check(sessionmaker, rules, care.patient.id, SELF_HARM)

    again = await raise_signal(sessionmaker, care, alert.id)

    assert again.id == alert.id
    assert again.level == "mandatory"
    # Срабатывание AI — медицинские данные: оно пишется, хотя уровень не меняется.
    assert again.occurrences == 2
    assert [(t.origin_ref, t.level) for t in again.triggers] == [
        ("submission:1", "mandatory"),
        (str(AI_RUN), "mandatory"),
    ]
    # Новые цитаты полного разбора доходят до кабинета без перезагрузки (#13).
    assert await notifications.settle() == [
        event("alert.created", alert, care.doctor.id),
        event("alert.updated", alert, care.doctor.id),
    ]


async def test_ai_signal_for_alert_in_work_is_appended(
    sessionmaker: async_sessionmaker[AsyncSession], rules: RuleBook, care: Care
) -> None:
    # Спека #95, раздел 3: AI-признаки дописываются к нерешённому сигналу.
    [alert] = await check(sessionmaker, rules, care.patient.id, HIGH_MOOD)
    await acknowledge(sessionmaker, care, alert.id)

    raised = await raise_signal(sessionmaker, care, alert.id)

    assert (raised.id, raised.level, raised.state) == (alert.id, "mandatory", "in_work")


async def test_ai_signal_for_resolved_alert_becomes_a_new_signal(
    sessionmaker: async_sessionmaker[AsyncSession], rules: RuleBook, care: Care
) -> None:
    [alert] = await check(sessionmaker, rules, care.patient.id, SELF_HARM)
    await resolve(sessionmaker, care, alert.id)

    fresh = await raise_signal(sessionmaker, care, alert.id)

    assert fresh.id != alert.id
    assert (fresh.origin_type, fresh.level) == ("ai_run", "mandatory")
    old = next(a for a in await patient_alerts(sessionmaker, care.patient.id) if a.id == alert.id)
    assert (old.state, len(old.triggers)) == ("resolved", 1)


async def test_resolve_seen_by_another_session_is_not_overwritten(
    sessionmaker: async_sessionmaker[AsyncSession], rules: RuleBook, care: Care
) -> None:
    [alert] = await check(sessionmaker, rules, care.patient.id, SELF_HARM)
    async with sessionmaker() as worker:
        # Объект сигнала остался в сессии worker со старыми полями: врач решил сигнал
        # в другой транзакции позже. Сервис ORM-объекты наружу не отдаёт, поэтому держим
        # его через модель напрямую.
        held = await worker.get(Alert, alert.id)
        assert held is not None and held.state == "new"
        await resolve(sessionmaker, care, alert.id)

        fresh = await alerts.raise_signal(
            worker,
            patient_id=care.patient.id,
            title="Признаки риска в дневнике",
            doctor_actions=["Связаться с пациентом"],
            evidence={"quote": "не вижу смысла"},
            ai_run_id=AI_RUN,
            alert_id=alert.id,
        )
        await worker.commit()

    assert fresh.id != alert.id
    old = next(a for a in await patient_alerts(sessionmaker, care.patient.id) if a.id == alert.id)
    assert (old.state, len(old.triggers)) == ("resolved", 1)


async def test_ai_cannot_raise_signal_of_another_patient(
    sessionmaker: async_sessionmaker[AsyncSession], rules: RuleBook, care: Care
) -> None:
    other = await make_care(sessionmaker, "other-doctor@example.com", "other@example.com")
    [foreign] = await check(sessionmaker, rules, other.patient.id, SELF_HARM)

    with pytest.raises(alerts.AlertNotFoundError):
        await raise_signal(sessionmaker, care, foreign.id)


@pytest.mark.parametrize(
    ("title", "doctor_actions"),
    [("", ["Связаться с пациентом"]), ("Признаки риска", []), ("Признаки риска", [" "])],
)
async def test_ai_signal_needs_title_and_actions(
    sessionmaker: async_sessionmaker[AsyncSession],
    care: Care,
    title: str,
    doctor_actions: list[str],
) -> None:
    async with sessionmaker() as session:
        with pytest.raises(ValueError, match="title and doctor_actions"):
            await alerts.raise_signal(
                session,
                patient_id=care.patient.id,
                title=title,
                doctor_actions=doctor_actions,
                evidence={},
                ai_run_id=AI_RUN,
            )


async def test_ai_signal_for_unknown_patient_is_rejected(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with sessionmaker() as session:
        with pytest.raises(alerts.PatientNotFoundError):
            await alerts.raise_signal(
                session,
                patient_id=uuid4(),
                title="Признаки риска в дневнике",
                doctor_actions=["Связаться с пациентом"],
                evidence={"quote": "не вижу смысла"},
                ai_run_id=AI_RUN,
            )
