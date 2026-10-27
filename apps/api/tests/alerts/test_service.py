"""Сигналы по правилам: создание, повторы, подтверждение, уровни (спека #12, разделы 3–6).

Правила — выдуманные, из tests/alerts/knowledge. События ловятся LISTEN на настоящем Postgres.
"""

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.modules.alerts import service as alerts
from clin_compass.modules.alerts.rules import RuleBook
from tests.alerts.conftest import (
    HIGH_MOOD,
    SELF_HARM,
    Care,
    Notifications,
    acknowledge,
    check,
    event,
    patient_alerts,
    resolve,
)

LOW_ANXIETY = {"test-anxiety": {"a1": 0, "a2": 1, "total": 1}}
HIGH_ANXIETY = {"test-anxiety": {"a1": 2, "a2": 2, "total": 4}}


async def test_firing_rule_creates_signal_for_lead_doctor_with_rule_snapshot(
    sessionmaker: async_sessionmaker[AsyncSession],
    rules: RuleBook,
    care: Care,
    notifications: Notifications,
) -> None:
    [alert] = await check(sessionmaker, rules, care.patient.id, SELF_HARM)

    assert alert.doctor_id == care.doctor.id
    assert (alert.origin_type, alert.rule_id, alert.rule_version) == ("rule", "test-self-harm", 1)
    assert (alert.kind, alert.level) == ("alert", "mandatory")
    assert alert.doctor_actions == ["Связаться с пациентом в тот же день"]
    assert [s.source for s in alert.sources] == ["test-guideline"]
    assert alert.status == "created"
    [trigger] = alert.triggers
    assert trigger.origin_ref == "submission:1"
    assert trigger.evidence == {"test-mood": {"q3": 2}}
    assert await notifications.wait_for(1) == [event("alert.created", alert, care.doctor.id)]


async def test_rule_that_does_not_fire_creates_nothing(
    sessionmaker: async_sessionmaker[AsyncSession], rules: RuleBook, care: Care
) -> None:
    fired = await check(sessionmaker, rules, care.patient.id, {"test-mood": {"q3": 0, "total": 0}})

    assert fired == []
    assert await patient_alerts(sessionmaker, care.patient.id) == []


async def test_repeat_before_acknowledge_appends_trigger_and_publishes_update(
    sessionmaker: async_sessionmaker[AsyncSession],
    rules: RuleBook,
    care: Care,
    notifications: Notifications,
) -> None:
    [first] = await check(sessionmaker, rules, care.patient.id, SELF_HARM, "submission:1")
    repeat = {"test-mood": {"q1": 0, "q2": 0, "q3": 3, "total": 3}}
    [again] = await check(sessionmaker, rules, care.patient.id, repeat, "submission:2")

    assert again.id == first.id
    assert again.occurrences == 2
    # Первое срабатывание — медицинские данные: оно остаётся в истории.
    assert [(t.origin_ref, t.evidence) for t in again.triggers] == [
        ("submission:1", {"test-mood": {"q3": 2}}),
        ("submission:2", {"test-mood": {"q3": 3}}),
    ]
    # Кабинет врача (#13) обновляет открытый сигнал без перезагрузки.
    assert await notifications.settle() == [
        event("alert.created", first, care.doctor.id),
        event("alert.updated", first, care.doctor.id),
    ]


async def test_repeat_while_in_work_appends_to_the_same_signal(
    sessionmaker: async_sessionmaker[AsyncSession], rules: RuleBook, care: Care
) -> None:
    # Спека #95, раздел 3: пока сигнал не решён, срабатывание дописывается к нему.
    [first] = await check(sessionmaker, rules, care.patient.id, SELF_HARM)
    await acknowledge(sessionmaker, care, first.id)

    [second] = await check(sessionmaker, rules, care.patient.id, SELF_HARM)

    assert (second.id, second.occurrences, second.state) == (first.id, 2, "in_work")


async def test_after_resolve_the_same_rule_creates_a_new_signal(
    sessionmaker: async_sessionmaker[AsyncSession], rules: RuleBook, care: Care
) -> None:
    [first] = await check(sessionmaker, rules, care.patient.id, SELF_HARM)
    await resolve(sessionmaker, care, first.id)

    [second] = await check(sessionmaker, rules, care.patient.id, SELF_HARM)

    assert second.id != first.id
    assert second.state == "new"
    assert len(await patient_alerts(sessionmaker, care.patient.id)) == 2


async def test_other_scale_submission_does_not_retrigger_old_answer(
    sessionmaker: async_sessionmaker[AsyncSession], rules: RuleBook, care: Care
) -> None:
    [alert] = await check(sessionmaker, rules, care.patient.id, SELF_HARM, "mood:1")
    # Через день пациент заполняет другую шкалу. В последних результатах всё ещё
    # старый ответ о самоповреждении, но новой отправки этой шкалы не было.
    latest = SELF_HARM | LOW_ANXIETY

    before_ack = await check(
        sessionmaker, rules, care.patient.id, latest, "anxiety:1", submitted="test-anxiety"
    )
    await acknowledge(sessionmaker, care, alert.id)
    after_ack = await check(
        sessionmaker, rules, care.patient.id, latest, "anxiety:2", submitted="test-anxiety"
    )

    assert (before_ack, after_ack) == ([], [])
    [only] = await patient_alerts(sessionmaker, care.patient.id)
    assert [t.origin_ref for t in only.triggers] == ["mood:1"]


async def test_rule_over_two_scales_fires_when_the_second_scale_arrives(
    sessionmaker: async_sessionmaker[AsyncSession], rules: RuleBook, care: Care
) -> None:
    [mood_only] = await check(sessionmaker, rules, care.patient.id, HIGH_MOOD, "mood:1")
    assert mood_only.rule_id == "test-high-mood"

    fired = await check(
        sessionmaker,
        rules,
        care.patient.id,
        HIGH_MOOD | HIGH_ANXIETY,
        "anxiety:1",
        submitted="test-anxiety",
    )

    # Правило по одной шкале настроения второй раз не срабатывает: его шкалу не отправляли.
    assert [a.rule_id for a in fired] == ["test-mood-and-anxiety"]
    assert fired[0].triggers[0].evidence == {
        "test-mood": {"total": 5},
        "test-anxiety": {"total": 4},
    }


async def test_check_after_insert_conflict_appends_to_the_signal_of_the_other_transaction(
    sessionmaker: async_sessionmaker[AsyncSession], rules: RuleBook, care: Care
) -> None:
    async with sessionmaker() as first:
        # Первая транзакция создала сигнал и ещё не закоммитила: вторая его не видит.
        await alerts.check_results(
            first,
            rules,
            patient_id=care.patient.id,
            submitted_scale="test-mood",
            results=SELF_HARM,
            origin_ref="s:1",
        )
        second = asyncio.create_task(check(sessionmaker, rules, care.patient.id, SELF_HARM, "s:2"))
        # Вставка второй ждёт исхода первой транзакции на уникальном индексе.
        await wait_for_lock_waiter(sessionmaker)
        await first.commit()
        [appended] = await second

    [alert] = await patient_alerts(sessionmaker, care.patient.id)
    assert appended.id == alert.id
    assert alert.occurrences == 2
    assert [t.origin_ref for t in alert.triggers] == ["s:1", "s:2"]


async def wait_for_lock_waiter(maker: async_sessionmaker[AsyncSession]) -> None:
    """Дождаться (не дольше 5 с), что какой-то запрос к БД ждёт блокировку."""
    query = text(
        "SELECT count(*) FROM pg_stat_activity "
        "WHERE datname = current_database() AND wait_event_type = 'Lock'"
    )
    async with asyncio.timeout(5):
        while True:
            async with maker() as session:
                if await session.scalar(query):
                    return
            await asyncio.sleep(0.01)


async def test_signal_for_unknown_patient_is_rejected(
    sessionmaker: async_sessionmaker[AsyncSession], rules: RuleBook
) -> None:
    with pytest.raises(alerts.PatientNotFoundError):
        await check(sessionmaker, rules, uuid4(), SELF_HARM)


async def test_concurrent_checks_create_one_signal(
    sessionmaker: async_sessionmaker[AsyncSession], rules: RuleBook, care: Care
) -> None:
    await asyncio.gather(
        *(check(sessionmaker, rules, care.patient.id, SELF_HARM, f"s:{i}") for i in range(5))
    )

    [alert] = await patient_alerts(sessionmaker, care.patient.id)
    assert alert.occurrences == 5
    assert len(alert.triggers) == 5


async def test_rolled_back_check_leaves_no_signal_and_no_event(
    sessionmaker: async_sessionmaker[AsyncSession],
    rules: RuleBook,
    care: Care,
    notifications: Notifications,
) -> None:
    await check(sessionmaker, rules, care.patient.id, SELF_HARM, commit=False)

    assert await patient_alerts(sessionmaker, care.patient.id) == []
    assert await notifications.settle() == []


async def test_repeated_acknowledge_keeps_first_time(
    sessionmaker: async_sessionmaker[AsyncSession], rules: RuleBook, care: Care
) -> None:
    [alert] = await check(sessionmaker, rules, care.patient.id, SELF_HARM)

    first = await acknowledge(sessionmaker, care, alert.id)
    second = await acknowledge(sessionmaker, care, alert.id)

    assert first.acknowledged_at is not None
    assert (second.acknowledged_at, second.acknowledged_by) == (
        first.acknowledged_at,
        care.doctor.id,
    )


def with_rule_level(rules: RuleBook, rule_id: str, level: str) -> RuleBook:
    """Книга правил, где у правила новая версия с другим уровнем."""
    changed = [
        r.model_copy(update={"version": r.version + 1, "level": level}) if r.id == rule_id else r
        for r in rules.rules
    ]
    return RuleBook(changed, rules.sources)


async def test_new_rule_version_with_higher_level_raises_open_signal(
    sessionmaker: async_sessionmaker[AsyncSession],
    rules: RuleBook,
    care: Care,
    notifications: Notifications,
) -> None:
    [alert] = await check(sessionmaker, rules, care.patient.id, HIGH_MOOD)
    assert alert.level == "desirable"

    [raised] = await check(
        sessionmaker,
        with_rule_level(rules, "test-high-mood", "mandatory"),
        care.patient.id,
        HIGH_MOOD,
    )

    assert raised.id == alert.id
    assert raised.level == "mandatory"
    assert raised.rule_version == 1  # снимок правила у сигнала не меняется
    assert [(t.rule_version, t.level) for t in raised.triggers] == [
        (1, "desirable"),
        (2, "mandatory"),
    ]
    assert await notifications.wait_for(2) == [
        event("alert.created", alert, care.doctor.id),
        event("alert.raised", alert, care.doctor.id),
    ]


async def test_new_rule_version_with_lower_level_does_not_lower_open_signal(
    sessionmaker: async_sessionmaker[AsyncSession],
    rules: RuleBook,
    care: Care,
    notifications: Notifications,
) -> None:
    [alert] = await check(sessionmaker, rules, care.patient.id, SELF_HARM)

    [again] = await check(
        sessionmaker,
        with_rule_level(rules, "test-self-harm", "desirable"),
        care.patient.id,
        SELF_HARM,
    )

    assert again.id == alert.id
    assert again.level == "mandatory"
    # Срабатывание помнит уровень своей версии правила, хотя сигнал его не понизил.
    assert [t.level for t in again.triggers] == ["mandatory", "desirable"]
    assert await notifications.settle() == [
        event("alert.created", alert, care.doctor.id),
        event("alert.updated", alert, care.doctor.id),
    ]
