"""Направление к психиатру на настоящей БД (спека #35, раздел 6).

Правила — `tests/alerts/knowledge`: `test-high-mood` — маршрутизация «желательно»,
`test-mood-and-anxiety` — маршрутизация «обязательно», `test-self-harm` — сигнал о риске.
"""

from datetime import timedelta
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

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

KIND = "alerts.referral"
BOTH_HIGH = {"test-mood": {"q3": 0, "total": 5}, "test-anxiety": {"total": 4}}


async def referrals(client: httpx.AsyncClient, care: Care) -> list[dict[str, Any]]:
    found = (await client.get(f"/api/v1/patients/{care.patient.id}/proposals")).json()
    return [p for p in found if p["kind"] == KIND]


async def decide(client: httpx.AsyncClient, care: Care, **body: Any) -> httpx.Response:
    body["alert_ids"] = [str(a) for a in body.get("alert_ids", [])]
    return await client.post(f"/api/v1/patients/{care.patient.id}/referral", json=body)


async def routing(
    sessionmaker: async_sessionmaker[AsyncSession], rules: RuleBook, care: Care
) -> list[Any]:
    """Сначала «желательно» по настроению, затем «обязательно» по двум шкалам."""
    [desirable] = await check(sessionmaker, rules, care.patient.id, HIGH_MOOD, "mood:1")
    fired = await check(
        sessionmaker, rules, care.patient.id, BOTH_HIGH, "anxiety:1", submitted="test-anxiety"
    )
    [mandatory] = [a for a in fired if a.rule_id == "test-mood-and-anxiety"]
    return [desirable, mandatory]


async def test_routing_signals_share_one_proposal(
    client: httpx.AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    rules: RuleBook,
    care: Care,
) -> None:
    [desirable, _] = await routing(sessionmaker, rules, care)
    await check(sessionmaker, rules, care.patient.id, SELF_HARM, "mood:2")
    await login(client, "doctor@example.com")

    [proposal] = await referrals(client, care)

    assert (proposal["status"], proposal["origin_type"]) == ("proposed", "rule")
    assert proposal["payload"]["alert_id"] == str(desirable.id)


async def test_confirm_resolves_all_routing_signals_with_a_comment(
    client: httpx.AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    rules: RuleBook,
    care: Care,
    notifications: Notifications,
) -> None:
    signals = await routing(sessionmaker, rules, care)
    [risk] = await check(sessionmaker, rules, care.patient.id, SELF_HARM, "mood:2")
    before = len(await notifications.wait_for(3))
    await login(client, "doctor@example.com")

    response = await decide(
        client,
        care,
        decision="confirm",
        alert_ids=[a.id for a in signals],
        comment="  Тревога и бессонница три недели  ",
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["proposal"]["status"], body["proposal"]["decision_comment"]) == (
        "confirmed",
        "Тревога и бессонница три недели",
    )
    assert [(a["state"], a["decision"]["decision_text"]) for a in body["alerts"]] == [
        ("resolved", "Направлен к психиатру")
    ] * 2
    open_alerts = (await client.get("/api/v1/alerts")).json()
    assert [a["id"] for a in open_alerts] == [str(risk.id)]
    events = (await notifications.wait_for(before + 2))[before:]
    assert events == [event("alert.updated", a, care.doctor.id) for a in signals]
    assert ("referral.confirm", care.patient.id, None) in await access_log(sessionmaker)


async def test_decline_needs_reason_and_comment(
    client: httpx.AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    rules: RuleBook,
    care: Care,
) -> None:
    [alert] = await check(sessionmaker, rules, care.patient.id, HIGH_MOOD)
    await login(client, "doctor@example.com")
    ids = [alert.id]

    no_reason = await decide(client, care, decision="decline", alert_ids=ids, comment="Текст")
    unknown = await decide(
        client, care, decision="decline", alert_ids=ids, reason_id="nope", comment="Текст"
    )
    no_comment = await decide(client, care, decision="decline", alert_ids=ids, reason_id="other")
    done = await decide(
        client, care, decision="decline", alert_ids=ids, reason_id="other", comment="Переезжает"
    )

    assert [r.status_code for r in (no_reason, unknown, no_comment)] == [422, 422, 422]
    assert done.status_code == 200, done.text
    assert done.json()["proposal"]["status"] == "rejected"
    assert done.json()["proposal"]["decision_comment"] == "Другое. Переезжает"
    [closed] = done.json()["alerts"]
    assert (closed["decision"]["decision_id"], closed["decision"]["reason"]) == (
        "other",
        "Переезжает",
    )


async def test_signals_changed_since_the_sheet_opened(
    client: httpx.AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    rules: RuleBook,
    care: Care,
) -> None:
    [desirable, _] = await routing(sessionmaker, rules, care)
    await login(client, "doctor@example.com")

    stale = await decide(client, care, decision="confirm", alert_ids=[desirable.id])

    # Врач видел только «желательно», а пришло ещё «обязательно»: ничего не меняется.
    assert stale.status_code == 409
    assert [a["state"] for a in (await client.get("/api/v1/alerts")).json()] == ["new", "new"]


async def test_new_firing_after_decision_gives_new_signal_and_proposal(
    client: httpx.AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    rules: RuleBook,
    care: Care,
) -> None:
    [first] = await check(sessionmaker, rules, care.patient.id, HIGH_MOOD, "mood:1")
    await login(client, "doctor@example.com")
    await decide(client, care, decision="confirm", alert_ids=[first.id])
    again = await decide(client, care, decision="confirm", alert_ids=[first.id])

    [second] = await check(sessionmaker, rules, care.patient.id, HIGH_MOOD, "mood:2")

    assert again.status_code == 409
    assert second.id != first.id
    assert [p["status"] for p in await referrals(client, care)] == ["confirmed", "proposed"]


async def test_signal_without_proposal_is_decided(
    client: httpx.AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    rules: RuleBook,
    care: Care,
) -> None:
    # Сигнал маршрутизации до #35 (сид, стенд): предложения у него нет.
    [alert] = await check(sessionmaker, rules, care.patient.id, HIGH_MOOD)
    async with sessionmaker() as session:
        await session.execute(
            text(
                "UPDATE proposals.proposals SET status = 'rejected', decided_by = :doctor,"
                " decided_at = now() WHERE kind = :kind"
            ),
            {"doctor": care.doctor.id, "kind": KIND},
        )
        await session.commit()
    await login(client, "doctor@example.com")

    response = await decide(client, care, decision="confirm", alert_ids=[alert.id])

    assert response.status_code == 200, response.text
    assert response.json()["proposal"]["status"] == "confirmed"


async def test_snooze_postpones_desirable_only(
    client: httpx.AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    rules: RuleBook,
    care: Care,
) -> None:
    [desirable] = await check(sessionmaker, rules, care.patient.id, HIGH_MOOD, "mood:1")
    await login(client, "doctor@example.com")
    until = (moscow_today() + timedelta(days=7)).isoformat()

    done = await decide(client, care, decision="snooze", alert_ids=[desirable.id], until=until)
    fired = await check(
        sessionmaker, rules, care.patient.id, BOTH_HIGH, "anxiety:1", submitted="test-anxiety"
    )
    [mandatory] = [a for a in fired if a.rule_id == "test-mood-and-anxiety"]
    refused = await decide(
        client, care, decision="snooze", alert_ids=[desirable.id, mandatory.id], until=until
    )

    assert done.status_code == 200, done.text
    assert [(a["state"], a["snoozed_until"]) for a in done.json()["alerts"]] == [("snoozed", until)]
    assert refused.status_code == 409
    assert [p["status"] for p in await referrals(client, care)] == ["proposed"]


async def test_common_routes_and_foreign_doctor_cannot_decide(
    client: httpx.AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    rules: RuleBook,
    care: Care,
) -> None:
    [alert] = await check(sessionmaker, rules, care.patient.id, HIGH_MOOD)
    await make_care(sessionmaker, "other-doctor@example.com", "other@example.com")
    await login(client, "doctor@example.com")
    [proposal] = await referrals(client, care)
    common = await client.post(f"/api/v1/proposals/{proposal['id']}/confirm", json={})
    await login(client, "other-doctor@example.com")

    foreign = await decide(client, care, decision="confirm", alert_ids=[alert.id])

    assert (common.status_code, foreign.status_code) == (409, 404)
    assert ("patient.referral.decide.denied", care.patient.id, None) in await access_log(
        sessionmaker
    )


async def test_confirm_also_closes_a_snoozed_signal(
    client: httpx.AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    rules: RuleBook,
    care: Care,
) -> None:
    [desirable] = await check(sessionmaker, rules, care.patient.id, HIGH_MOOD, "mood:1")
    await login(client, "doctor@example.com")
    until = (moscow_today() + timedelta(days=3)).isoformat()
    await decide(client, care, decision="snooze", alert_ids=[desirable.id], until=until)

    response = await decide(client, care, decision="confirm", alert_ids=[desirable.id])

    assert response.status_code == 200, response.text
    assert [a["state"] for a in response.json()["alerts"]] == ["resolved"]
