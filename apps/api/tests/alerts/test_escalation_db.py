"""Эскалация «Обязательно» на настоящей БД (спека #56, раздел 7).

Правила — `tests/alerts/knowledge`: `test-self-harm` — «обязательно», `test-high-mood` —
«желательно». Шаги выполняются вызовом `chain.run` с заданным временем: worker не нужен.
"""

from datetime import timedelta
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.modules.alerts import chain, deputies
from clin_compass.modules.alerts.lifecycle import moscow_today
from clin_compass.modules.alerts.rules import RuleBook
from clin_compass.modules.alerts.schemas import AlertOut, DeputyIn
from clin_compass.modules.identity import service as identity
from clin_compass.modules.identity.schemas import Role, UserOut
from tests.alerts.conftest import (
    HIGH_MOOD,
    SELF_HARM,
    Care,
    Notifications,
    add_patient,
    check,
)
from tests.alerts.test_api import access_log, login
from tests.alerts.test_raise_signal import raise_signal
from tests.live import PASSWORD

MINUTES = (15, 30, 60)
REASON = "Связались, риск не высокий"


async def user(maker: async_sessionmaker[AsyncSession], email: str, role: Role) -> UserOut:
    async with maker() as session:
        created = await identity.create_user(
            session, email=email, password=PASSWORD, role=role, full_name=f"Синтетический {email}"
        )
        await session.commit()
    return created


async def assign(maker: async_sessionmaker[AsyncSession], care: Care, deputy: UserOut) -> None:
    admin = await user(maker, "admin@example.com", Role.ADMIN)
    async with maker() as session:
        await deputies.assign(
            session,
            admin.id,
            DeputyIn(
                doctor_id=care.doctor.id,
                deputy_id=deputy.id,
                starts_on=moscow_today(),
                ends_on=moscow_today() + timedelta(days=6),
                basis="Приказ № 1 от 20.10.2026",
            ),
        )
        await session.commit()


async def urgent(maker: async_sessionmaker[AsyncSession], rules: RuleBook, care: Care) -> AlertOut:
    [alert] = await check(maker, rules, care.patient.id, SELF_HARM)
    assert alert.urgent_since is not None
    return alert


async def step(
    maker: async_sessionmaker[AsyncSession], alert: AlertOut, n: int, after: int
) -> list[int]:
    """Шаг `n` через `after` минут от `urgent_since`."""
    assert alert.urgent_since is not None
    async with maker() as session:
        ran = await chain.run(
            session, alert.id, n, MINUTES, alert.urgent_since + timedelta(minutes=after)
        )
        await session.commit()
    return ran


async def jobs(maker: async_sessionmaker[AsyncSession]) -> list[tuple[str, Any]]:
    async with maker() as session:
        rows = await session.execute(
            text(
                "SELECT dedupe_key, execute_after - now() FROM pgq.pgqueuer"
                " WHERE entrypoint = 'alerts.escalate' ORDER BY id"
            )
        )
        return [(r[0], r[1]) for r in rows]


async def escalations(maker: async_sessionmaker[AsyncSession], alert_id: UUID) -> list[Any]:
    async with maker() as session:
        rows = await session.execute(
            text(
                "SELECT step, recipient_id FROM alerts.actions"
                " WHERE alert_id = :a AND action = 'escalate' ORDER BY id"
            ),
            {"a": alert_id},
        )
        return [tuple(r) for r in rows]


async def grants(maker: async_sessionmaker[AsyncSession]) -> list[Any]:
    async with maker() as session:
        rows = await session.execute(
            text("SELECT doctor_id, end_reason FROM patients.access_grants ORDER BY granted_at")
        )
        return [tuple(r) for r in rows]


async def test_mandatory_alert_plans_the_chain(
    sessionmaker: async_sessionmaker[AsyncSession], rules: RuleBook, care: Care
) -> None:
    alert = await urgent(sessionmaker, rules, care)

    planned = await jobs(sessionmaker)
    await step(sessionmaker, alert, chain.PLAN, 0)

    assert [key for key, _ in planned] == [f"alert:{alert.id}:step:0"]
    [_, (key, wait)] = await jobs(sessionmaker)
    assert key == f"alert:{alert.id}:step:1"
    assert timedelta(minutes=13) < wait <= timedelta(minutes=15)


async def test_desirable_alert_has_no_timer_until_it_is_raised(
    sessionmaker: async_sessionmaker[AsyncSession], rules: RuleBook, care: Care
) -> None:
    [alert] = await check(sessionmaker, rules, care.patient.id, HIGH_MOOD)
    assert alert.urgent_since is None
    assert await jobs(sessionmaker) == []

    raised = await raise_signal(sessionmaker, care, alert.id)

    # Отсчёт — от подъёма до «Обязательно», не от создания.
    assert raised.urgent_since is not None
    assert raised.urgent_since > alert.created_at
    assert [key for key, _ in await jobs(sessionmaker)] == [f"alert:{alert.id}:step:0"]


async def test_step_1_repeats_to_the_lead_doctor(
    sessionmaker: async_sessionmaker[AsyncSession],
    rules: RuleBook,
    care: Care,
    notifications: Notifications,
) -> None:
    alert = await urgent(sessionmaker, rules, care)

    assert await step(sessionmaker, alert, 1, 15) == [1]
    assert await step(sessionmaker, alert, 1, 16) == []  # повтор задачи ничего не меняет

    assert await escalations(sessionmaker, alert.id) == [(1, care.doctor.id)]
    events = await notifications.wait_for(2)
    assert {
        "to": [str(care.doctor.id)],
        "type": "alert.escalated",
        "data": {"alert_id": str(alert.id)},
    } in events
    assert [key for key, _ in await jobs(sessionmaker)][-1] == f"alert:{alert.id}:step:2"


async def test_taken_alert_does_not_escalate(
    client: httpx.AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    rules: RuleBook,
    care: Care,
) -> None:
    alert = await urgent(sessionmaker, rules, care)
    await login(client, "doctor@example.com")
    await client.post(f"/api/v1/alerts/{alert.id}/acknowledge")

    assert await step(sessionmaker, alert, 1, 15) == []
    assert await escalations(sessionmaker, alert.id) == []


async def test_without_deputy_admins_hear_at_30_minutes(
    sessionmaker: async_sessionmaker[AsyncSession],
    rules: RuleBook,
    care: Care,
    notifications: Notifications,
) -> None:
    admin = await user(sessionmaker, "admin@example.com", Role.ADMIN)
    alert = await urgent(sessionmaker, rules, care)
    await step(sessionmaker, alert, 1, 15)

    assert await step(sessionmaker, alert, 2, 30) == [2, 3]

    assert await escalations(sessionmaker, alert.id) == [(1, care.doctor.id), (2, None), (3, None)]
    events = await notifications.settle()
    assert {
        "to": [str(admin.id)],
        "type": "escalation.created",
        "data": {"alert_id": str(alert.id)},
    } in events
    # Шага после третьего нет.
    assert all(not key.endswith("step:4") for key, _ in await jobs(sessionmaker))


async def test_deputy_reads_the_minimal_set_only(
    client: httpx.AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    rules: RuleBook,
    care: Care,
) -> None:
    deputy = await user(sessionmaker, "deputy@example.com", Role.DOCTOR)
    await assign(sessionmaker, care, deputy)
    other = await add_patient(sessionmaker, care.doctor.id, "second@example.com")
    alert = await urgent(sessionmaker, rules, care)
    await step(sessionmaker, alert, 1, 15)

    assert await step(sessionmaker, alert, 2, 30) == [2]

    await login(client, "deputy@example.com")
    base = f"/api/v1/patients/{care.patient.id}"
    profile = await client.get(base)
    assert profile.status_code == 200
    assert (profile.json()["access"], profile.json()["email"]) == ("deputy", "")
    assert [a["id"] for a in (await client.get(f"{base}/alerts")).json()] == [str(alert.id)]
    assert (await client.get(f"{base}/assessments")).status_code == 200
    assert (await client.get(f"{base}/proposals")).status_code == 404
    assert (await client.get(f"{base}/documents")).status_code == 404
    assert (await client.get(f"/api/v1/patients/{other.id}")).status_code == 404
    [listed] = (await client.get("/api/v1/alerts")).json()
    assert (listed["id"], listed["as_deputy"]) == (str(alert.id), True)
    log = [entry[0] for entry in await access_log(sessionmaker)]
    assert {"patient.view.deputy", "assessments.list.deputy", "alerts.list_open.deputy"} <= set(log)
    assert {"patient.proposals.list.denied", "patient.documents.list.denied"} <= set(log)


async def test_deputy_takes_the_alert_and_step_3_never_comes(
    client: httpx.AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    rules: RuleBook,
    care: Care,
) -> None:
    deputy = await user(sessionmaker, "deputy@example.com", Role.DOCTOR)
    await assign(sessionmaker, care, deputy)
    alert = await urgent(sessionmaker, rules, care)
    await step(sessionmaker, alert, 2, 30)
    await login(client, "deputy@example.com")

    taken = await client.post(f"/api/v1/alerts/{alert.id}/acknowledge")
    referral = await client.post(
        f"/api/v1/patients/{care.patient.id}/referral",
        json={"decision": "confirm", "alert_ids": [str(alert.id)]},
    )

    assert taken.status_code == 200
    assert (taken.json()["state"], taken.json()["acknowledged_by"]) == ("in_work", str(deputy.id))
    assert referral.status_code == 404  # направление — только ведущий врач
    assert await step(sessionmaker, alert, 3, 60) == []
    assert ("alert.acknowledge.deputy", care.patient.id, str(alert.id)) in await access_log(
        sessionmaker
    )


async def test_resolution_closes_deputy_access(
    client: httpx.AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    rules: RuleBook,
    care: Care,
) -> None:
    deputy = await user(sessionmaker, "deputy@example.com", Role.DOCTOR)
    await assign(sessionmaker, care, deputy)
    alert = await urgent(sessionmaker, rules, care)
    await step(sessionmaker, alert, 2, 30)
    await login(client, "deputy@example.com")

    done = await client.post(
        f"/api/v1/alerts/{alert.id}/resolve",
        json={"decision_id": "risk-not-high", "reason": REASON},
    )

    assert done.status_code == 200
    assert done.json()["deputy_access"]["end_reason"] == "resolved"
    assert await grants(sessionmaker) == [(deputy.id, "resolved")]
    assert (await client.get(f"/api/v1/patients/{care.patient.id}")).status_code == 404


async def test_lead_action_closes_deputy_access(
    client: httpx.AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    rules: RuleBook,
    care: Care,
) -> None:
    deputy = await user(sessionmaker, "deputy@example.com", Role.DOCTOR)
    await assign(sessionmaker, care, deputy)
    alert = await urgent(sessionmaker, rules, care)
    await step(sessionmaker, alert, 2, 30)
    await login(client, "doctor@example.com")

    await client.post(f"/api/v1/alerts/{alert.id}/acknowledge")

    assert await grants(sessionmaker) == [(deputy.id, "lead_took")]


async def test_access_expires_by_time(
    client: httpx.AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    rules: RuleBook,
    care: Care,
) -> None:
    deputy = await user(sessionmaker, "deputy@example.com", Role.DOCTOR)
    await assign(sessionmaker, care, deputy)
    alert = await urgent(sessionmaker, rules, care)
    await step(sessionmaker, alert, 2, 30)
    async with sessionmaker() as session:
        await session.execute(
            text(
                "UPDATE patients.access_grants SET granted_at = now() - interval '13 hours',"
                " expires_at = now() - interval '1 hour'"
            )
        )
        await session.commit()
    await login(client, "deputy@example.com")

    assert (await client.get(f"/api/v1/patients/{care.patient.id}")).status_code == 404
    assert (await client.get("/api/v1/alerts")).json() == []


async def test_admin_endpoints(
    client: httpx.AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    rules: RuleBook,
    care: Care,
) -> None:
    deputy = await user(sessionmaker, "deputy@example.com", Role.DOCTOR)
    await user(sessionmaker, "admin@example.com", Role.ADMIN)
    alert = await urgent(sessionmaker, rules, care)
    await step(sessionmaker, alert, 2, 30)
    body = {
        "doctor_id": str(care.doctor.id),
        "deputy_id": str(deputy.id),
        "starts_on": moscow_today().isoformat(),
        "ends_on": (moscow_today() + timedelta(days=3)).isoformat(),
        "basis": "График отпусков",
    }

    await login(client, "doctor@example.com")
    assert (await client.get("/api/v1/admin/escalations")).status_code == 403

    await login(client, "admin@example.com")
    created = await client.post("/api/v1/admin/deputies", json=body)
    overlap = await client.post("/api/v1/admin/deputies", json=body)
    doctors = {d["id"]: d for d in (await client.get("/api/v1/admin/doctors")).json()}
    [escalation] = (await client.get("/api/v1/admin/escalations")).json()
    reviewed = await client.post(f"/api/v1/admin/escalations/{alert.id}/review")

    assert (created.status_code, overlap.status_code) == (201, 409)
    assert doctors[str(care.doctor.id)]["no_deputy"] is False
    assert doctors[str(deputy.id)]["no_deputy"] is True
    assert escalation["number"] == str(alert.id)[:8]
    assert [s["step"] for s in escalation["steps"]] == [2, 3]
    # Без данных пациента: ни ФИО, ни заголовка, ни уровня.
    assert not {"patient_id", "title", "level", "full_name"} & set(escalation)
    assert "Пациент" not in str(escalation)
    assert reviewed.status_code == 204
    assert (await client.get("/api/v1/admin/escalations")).json() == []
    assert len((await client.get("/api/v1/admin/escalations?status=all")).json()) == 1


async def test_revoking_the_assignment_closes_access(
    client: httpx.AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    rules: RuleBook,
    care: Care,
) -> None:
    deputy = await user(sessionmaker, "deputy@example.com", Role.DOCTOR)
    await assign(sessionmaker, care, deputy)
    alert = await urgent(sessionmaker, rules, care)
    await step(sessionmaker, alert, 2, 30)
    await login(client, "admin@example.com")
    [assignment] = (await client.get("/api/v1/admin/deputies")).json()

    revoked = await client.post(f"/api/v1/admin/deputies/{assignment['id']}/revoke")

    assert revoked.status_code == 200
    assert revoked.json()["revoked_at"] is not None
    assert await grants(sessionmaker) == [(deputy.id, "revoked")]


async def test_deputy_hears_the_alert_only_while_access_lasts(
    client: httpx.AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    rules: RuleBook,
    care: Care,
    notifications: Notifications,
) -> None:
    """#170: события по сигналу — заместителю, только пока действует его доступ."""
    deputy = await user(sessionmaker, "deputy@example.com", Role.DOCTOR)
    await assign(sessionmaker, care, deputy)
    alert = await urgent(sessionmaker, rules, care)
    await step(sessionmaker, alert, 2, 30)
    await login(client, "doctor@example.com")

    await client.post(f"/api/v1/alerts/{alert.id}/acknowledge")
    await client.post(
        f"/api/v1/alerts/{alert.id}/resolve",
        json={"decision_id": "risk-not-high", "reason": REASON},
    )

    updates = [e["to"] for e in await notifications.settle() if e["type"] == "alert.updated"]
    # Взятие в работу закрывает доступ и ещё доходит до заместителя: его экран убирает сигнал.
    assert updates == [[str(care.doctor.id), str(deputy.id)], [str(care.doctor.id)]]


async def test_deputy_hears_the_resolution_that_closes_access(
    client: httpx.AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    rules: RuleBook,
    care: Care,
    notifications: Notifications,
) -> None:
    deputy = await user(sessionmaker, "deputy@example.com", Role.DOCTOR)
    await assign(sessionmaker, care, deputy)
    alert = await urgent(sessionmaker, rules, care)
    await step(sessionmaker, alert, 2, 30)
    await login(client, "deputy@example.com")

    await client.post(
        f"/api/v1/alerts/{alert.id}/resolve",
        json={"decision_id": "risk-not-high", "reason": REASON},
    )

    updates = [e["to"] for e in await notifications.settle() if e["type"] == "alert.updated"]
    assert updates == [[str(care.doctor.id), str(deputy.id)]]


async def test_admins_hear_steps_before_the_third(
    client: httpx.AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    rules: RuleBook,
    care: Care,
    notifications: Notifications,
) -> None:
    """#170: таблица эскалаций показывает сигнал с шага 1 — шаг и его изменения её обновляют."""
    admin = await user(sessionmaker, "admin@example.com", Role.ADMIN)
    alert = await urgent(sessionmaker, rules, care)
    await step(sessionmaker, alert, 1, 15)
    await login(client, "doctor@example.com")

    await client.post(f"/api/v1/alerts/{alert.id}/acknowledge")

    to_admins = [e for e in await notifications.settle() if e["to"] == [str(admin.id)]]
    assert (
        to_admins
        == [
            {
                "to": [str(admin.id)],
                "type": "escalation.updated",
                "data": {"alert_id": str(alert.id)},
            }
        ]
        * 2
    )
