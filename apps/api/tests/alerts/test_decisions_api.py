"""Действия врача по сигналу на настоящей БД (спека #95, раздел 6).

Правила — `tests/alerts/knowledge`: `test-self-harm` — сигнал о риске «обязательно» (`alert`),
`test-high-mood` — маршрутизация «желательно». Решения и отсрочка — `signal-decisions.yaml`.
"""

from datetime import timedelta
from typing import Any
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.modules.alerts import service as alerts
from clin_compass.modules.alerts.lifecycle import moscow_today
from clin_compass.modules.alerts.rules import RuleBook
from tests.alerts.conftest import (
    HIGH_MOOD,
    SELF_HARM,
    Care,
    Notifications,
    check,
    event,
    make_care,
)
from tests.alerts.test_api import access_log, login

REASON = "Связались, риск не высокий, контакт через неделю"


async def post(client: httpx.AsyncClient, alert_id: object, action: str, **body: Any) -> Any:
    return await client.post(f"/api/v1/alerts/{alert_id}/{action}", json=body or None)


def in_days(n: int) -> str:
    return (moscow_today() + timedelta(days=n)).isoformat()


async def test_mandatory_needs_a_reason(
    client: httpx.AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    rules: RuleBook,
    care: Care,
) -> None:
    [alert] = await check(sessionmaker, rules, care.patient.id, SELF_HARM)
    await login(client, "doctor@example.com")

    blank = await post(client, alert.id, "resolve", decision_id="risk-not-high", reason="  ")
    wrong = await post(client, alert.id, "resolve", decision_id="drug-stopped", reason=REASON)
    done = await post(client, alert.id, "resolve", decision_id="risk-not-high", reason=REASON)
    again = await post(client, alert.id, "resolve", decision_id="risk-not-high", reason=REASON)

    assert [blank.status_code, wrong.status_code, done.status_code, again.status_code] == [
        422,
        422,
        200,
        409,
    ]
    body = done.json()
    assert (body["state"], body["resolved_by"]) == ("resolved", str(care.doctor.id))
    # Решить можно без «Взять в работу»: это тоже значит, что врач занялся сигналом.
    assert body["acknowledged_by"] == str(care.doctor.id)
    assert body["decision"]["decision_text"] == "Риск не высокий: наблюдение продолжается"
    assert body["decision"]["reason"] == REASON
    assert str(care.doctor.id) in body["actors"]
    assert ("alert.resolve", care.patient.id, str(alert.id)) in await access_log(sessionmaker)


async def test_history_records_every_action_in_order(
    client: httpx.AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    rules: RuleBook,
    care: Care,
) -> None:
    [alert] = await check(sessionmaker, rules, care.patient.id, SELF_HARM)
    await login(client, "doctor@example.com")

    await post(client, alert.id, "acknowledge")
    await post(client, alert.id, "acknowledge")
    contact = await post(client, alert.id, "no-contact")
    done = await post(client, alert.id, "resolve", decision_id="risk-not-high", reason=REASON)

    assert contact.json()["state"] == "in_work"
    # Повторное «Взять в работу» ничего не меняет и в историю не пишется.
    assert [a["action"] for a in done.json()["actions"]] == ["acknowledge", "no_contact", "resolve"]


async def test_every_action_publishes_an_update(
    client: httpx.AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    rules: RuleBook,
    care: Care,
    notifications: Notifications,
) -> None:
    # Спека #95, раздел 3: кабинет врача обновляет списки без перезагрузки.
    [alert] = await check(sessionmaker, rules, care.patient.id, SELF_HARM)
    [desirable] = await check(sessionmaker, rules, care.patient.id, HIGH_MOOD, "submission:2")
    await notifications.wait_for(2)
    await login(client, "doctor@example.com")

    await post(client, alert.id, "acknowledge")
    await post(client, alert.id, "no-contact")
    await post(client, alert.id, "resolve", decision_id="risk-not-high", reason=REASON)
    await post(client, desirable.id, "snooze", until=in_days(3))

    events = (await notifications.wait_for(6))[2:]
    assert events == [event("alert.updated", alert, care.doctor.id)] * 3 + [
        event("alert.updated", desirable, care.doctor.id)
    ]


async def test_history_is_append_only(
    sessionmaker: async_sessionmaker[AsyncSession], rules: RuleBook, care: Care
) -> None:
    [alert] = await check(sessionmaker, rules, care.patient.id, SELF_HARM)
    async with sessionmaker() as session:
        await alerts.acknowledge(session, doctor_id=care.doctor.id, alert_id=alert.id)
        await session.commit()

    for statement in ("UPDATE alerts.actions SET reason = 'x'", "DELETE FROM alerts.actions"):
        async with sessionmaker() as session:
            with pytest.raises(DBAPIError, match="keeps medical data"):
                await session.execute(text(statement))


async def test_desirable_is_snoozed_within_limits(
    client: httpx.AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    rules: RuleBook,
    care: Care,
) -> None:
    [alert] = await check(sessionmaker, rules, care.patient.id, HIGH_MOOD)
    [mandatory] = await check(sessionmaker, rules, care.patient.id, SELF_HARM, "submission:2")
    await login(client, "doctor@example.com")

    too_far = await post(client, alert.id, "snooze", until=in_days(15))
    today = await post(client, alert.id, "snooze", until=in_days(0))
    refused = await post(client, mandatory.id, "snooze", until=in_days(3))
    snoozed = await post(client, alert.id, "snooze", until=in_days(7))
    routing = await post(client, alert.id, "resolve", decision_id="risk-not-high", reason=REASON)

    assert [too_far.status_code, today.status_code, refused.status_code] == [422, 422, 409]
    assert snoozed.status_code == 200
    assert (snoozed.json()["state"], snoozed.json()["snoozed_until"]) == ("snoozed", in_days(7))
    # Маршрутизацию решает направление к психиатру (#35).
    assert routing.status_code == 409


async def test_expired_snooze_is_new_again(
    client: httpx.AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    rules: RuleBook,
    care: Care,
) -> None:
    [alert] = await check(sessionmaker, rules, care.patient.id, HIGH_MOOD)
    await login(client, "doctor@example.com")
    await post(client, alert.id, "snooze", until=in_days(3))
    async with sessionmaker() as session:
        await session.execute(
            text("UPDATE alerts.alerts SET snoozed_until = :day WHERE id = :id"),
            {"day": moscow_today(), "id": alert.id},
        )
        await session.commit()

    [listed] = (await client.get("/api/v1/alerts")).json()

    assert listed["state"] == "new"


async def test_raised_level_cancels_snooze(
    client: httpx.AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    rules: RuleBook,
    care: Care,
) -> None:
    [alert] = await check(sessionmaker, rules, care.patient.id, HIGH_MOOD)
    await login(client, "doctor@example.com")
    await post(client, alert.id, "snooze", until=in_days(7))

    async with sessionmaker() as session:
        raised = await alerts.raise_signal(
            session,
            patient_id=care.patient.id,
            title="Признаки риска в дневнике",
            doctor_actions=["Связаться с пациентом"],
            evidence={"quote": "не вижу смысла"},
            ai_run_id=uuid4(),
            alert_id=alert.id,
        )
        await session.commit()

    assert (raised.level, raised.state, raised.snoozed_until) == ("mandatory", "new", None)
    assert [(a.action, a.actor_id) for a in raised.actions][-1] == ("unsnooze", None)


async def test_list_open_and_resolved_by_patient(
    client: httpx.AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    rules: RuleBook,
    care: Care,
) -> None:
    [resolved] = await check(sessionmaker, rules, care.patient.id, SELF_HARM)
    [open_] = await check(sessionmaker, rules, care.patient.id, HIGH_MOOD, "submission:2")
    other = await make_care(sessionmaker, "other-doctor@example.com", "other@example.com")
    await login(client, "doctor@example.com")
    await post(client, resolved.id, "resolve", decision_id="risk-not-high", reason=REASON)

    by_status = {
        status: [
            a["id"]
            for a in (
                await client.get(
                    "/api/v1/alerts", params={"status": status, "patient_id": str(care.patient.id)}
                )
            ).json()
        ]
        for status in ("open", "resolved")
    }
    foreign = await client.get("/api/v1/alerts", params={"patient_id": str(other.patient.id)})

    assert by_status == {"open": [str(open_.id)], "resolved": [str(resolved.id)]}
    assert foreign.json() == []
    assert ("alerts.list_open.denied", None, str(other.patient.id)) in await access_log(
        sessionmaker
    )


async def test_foreign_alert_actions_are_not_found(
    client: httpx.AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    rules: RuleBook,
    care: Care,
) -> None:
    other = await make_care(sessionmaker, "other-doctor@example.com", "other@example.com")
    [foreign] = await check(sessionmaker, rules, other.patient.id, HIGH_MOOD)
    await login(client, "doctor@example.com")

    responses = [
        await post(client, foreign.id, "snooze", until=in_days(3)),
        await post(client, foreign.id, "no-contact"),
        await post(client, foreign.id, "resolve", decision_id="risk-not-high", reason=REASON),
    ]

    assert [r.status_code for r in responses] == [404, 404, 404]
    denied = [row[0] for row in await access_log(sessionmaker)]
    assert denied == ["alert.snooze.denied", "alert.no_contact.denied", "alert.resolve.denied"]


async def test_decisions_and_thresholds(client: httpx.AsyncClient, care: Care) -> None:
    await login(client, "doctor@example.com")

    decisions = (await client.get("/api/v1/alerts/decisions")).json()
    thresholds = (
        await client.get("/api/v1/alerts/thresholds", params={"scale": "test-mood"})
    ).json()

    assert [d["id"] for d in decisions["safety"]] == ["risk-not-high"]
    assert decisions["snooze"] == {"max_days": 14, "presets_days": [3, 7, 14]}
    assert {(t["rule_id"], t["level"]) for t in thresholds} >= {
        ("test-self-harm", "mandatory"),
        ("test-high-mood", "desirable"),
    }
