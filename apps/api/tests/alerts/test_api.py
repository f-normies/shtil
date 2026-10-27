"""HTTP API сигналов: только врач и только свои сигналы; доставка и подтверждение (спека #12, 5)."""

from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from structlog.testing import capture_logs

from clin_compass.modules.alerts import service as alerts
from clin_compass.modules.alerts.rules import RuleBook
from clin_compass.modules.alerts.schemas import AlertOut
from tests.alerts.conftest import SELF_HARM, Care, add_patient, make_care, patient_alerts
from tests.live import PASSWORD


async def login(client: httpx.AsyncClient, email: str) -> None:
    client.cookies.clear()
    response = await client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200


async def fire(
    maker: async_sessionmaker[AsyncSession], rules: RuleBook, patient_id: UUID
) -> AlertOut:
    async with maker() as session:
        [alert] = await alerts.check_results(
            session,
            rules,
            patient_id=patient_id,
            submitted_scale="test-mood",
            results=SELF_HARM,
            origin_ref="submission:1",
        )
        await session.commit()
        return alert


async def access_log(maker: async_sessionmaker[AsyncSession]) -> list[tuple[object, ...]]:
    async with maker() as session:
        rows = await session.execute(
            text("SELECT action, patient_id, subject FROM identity.access_log ORDER BY id")
        )
        return [tuple(r) for r in rows]


async def test_doctor_sees_only_own_open_alerts(
    client: httpx.AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    rules: RuleBook,
    care: Care,
) -> None:
    other = await make_care(sessionmaker, "other-doctor@example.com", "other@example.com")
    own = await fire(sessionmaker, rules, care.patient.id)
    await fire(sessionmaker, rules, other.patient.id)
    await login(client, "doctor@example.com")

    response = await client.get("/api/v1/alerts", params={"status": "open"})

    assert response.status_code == 200
    body = response.json()
    assert [a["id"] for a in body] == [str(own.id)]
    assert body[0]["status"] == "created"
    assert body[0]["triggers"][0]["evidence"] == {"test-mood": {"q3": 2}}
    assert ("alerts.list_open", care.patient.id, None) in await access_log(sessionmaker)


async def test_open_alerts_list_is_logged_once_per_patient(
    client: httpx.AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    rules: RuleBook,
    care: Care,
) -> None:
    second = await add_patient(sessionmaker, care.doctor.id, "second@example.com")
    await login(client, "doctor@example.com")
    await client.get("/api/v1/alerts")
    await fire(sessionmaker, rules, care.patient.id)
    await fire(sessionmaker, rules, second.id)
    await fire(sessionmaker, rules, second.id)

    await client.get("/api/v1/alerts")

    listed = [row for row in await access_log(sessionmaker) if row[0] == "alerts.list_open"]
    # Пустой список — одна запись без пациента; дальше — по записи на каждого пациента.
    assert listed == [
        ("alerts.list_open", None, None),
        *[("alerts.list_open", p, None) for p in sorted([care.patient.id, second.id])],
    ]


async def test_delivery_is_recorded_once_with_channel(
    client: httpx.AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    rules: RuleBook,
    care: Care,
) -> None:
    alert = await fire(sessionmaker, rules, care.patient.id)
    await login(client, "doctor@example.com")

    with capture_logs() as logs:
        first = await client.post(f"/api/v1/alerts/{alert.id}/delivered", params={"via": "ws"})
        [delivered] = (await client.get("/api/v1/alerts", params={"status": "open"})).json()
        again = await client.post(f"/api/v1/alerts/{alert.id}/delivered", params={"via": "rest"})
        [unchanged] = (await client.get("/api/v1/alerts", params={"status": "open"})).json()

    assert (first.status_code, again.status_code) == (204, 204)
    # Задержка для метрики 1 пишется в лог один раз, при первой доставке.
    [logged] = [entry for entry in logs if entry["event"] == "alert.delivered"]
    assert (logged["alert_id"], logged["via"]) == (str(alert.id), "ws")
    assert isinstance(logged["latency_ms"], int)
    assert delivered["status"] == "delivered"
    assert delivered["delivered_via"] == "ws"
    assert unchanged["delivered_at"] == delivered["delivered_at"]
    assert unchanged["delivered_via"] == "ws"


async def test_delivery_channel_must_be_ws_or_rest(
    client: httpx.AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    rules: RuleBook,
    care: Care,
) -> None:
    alert = await fire(sessionmaker, rules, care.patient.id)
    await login(client, "doctor@example.com")

    response = await client.post(f"/api/v1/alerts/{alert.id}/delivered", params={"via": "sms"})

    assert response.status_code == 422


async def test_acknowledge_takes_alert_into_work_and_is_logged(
    client: httpx.AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    rules: RuleBook,
    care: Care,
) -> None:
    alert = await fire(sessionmaker, rules, care.patient.id)
    await login(client, "doctor@example.com")

    response = await client.post(f"/api/v1/alerts/{alert.id}/acknowledge")
    still_open = (await client.get("/api/v1/alerts", params={"status": "open"})).json()

    assert response.status_code == 200
    assert (response.json()["status"], response.json()["state"]) == ("acknowledged", "in_work")
    assert response.json()["acknowledged_by"] == str(care.doctor.id)
    # Взятый в работу сигнал ещё не решён: он остаётся среди открытых (спека #95).
    assert [a["id"] for a in still_open] == [str(alert.id)]
    log = await access_log(sessionmaker)
    assert ("alert.acknowledge", care.patient.id, str(alert.id)) in log


async def test_foreign_alert_is_not_found(
    client: httpx.AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    rules: RuleBook,
    care: Care,
) -> None:
    other = await make_care(sessionmaker, "other-doctor@example.com", "other@example.com")
    foreign = await fire(sessionmaker, rules, other.patient.id)
    await login(client, "doctor@example.com")

    delivered = await client.post(f"/api/v1/alerts/{foreign.id}/delivered", params={"via": "ws"})
    acknowledged = await client.post(f"/api/v1/alerts/{foreign.id}/acknowledge")
    listed = await client.get(f"/api/v1/patients/{other.patient.id}/alerts")

    assert (delivered.status_code, acknowledged.status_code, listed.status_code) == (404, 404, 404)
    # Отказ пишется в журнал, как в owned_patient: пациента сигнала не раскрываем.
    assert await access_log(sessionmaker) == [
        ("alert.delivered.denied", None, str(foreign.id)),
        ("alert.acknowledge.denied", None, str(foreign.id)),
        ("patient.alerts.list.denied", other.patient.id, None),
    ]


async def test_unknown_alert_gives_the_same_404_as_foreign(
    client: httpx.AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    rules: RuleBook,
    care: Care,
) -> None:
    other = await make_care(sessionmaker, "other-doctor@example.com", "other@example.com")
    foreign = await fire(sessionmaker, rules, other.patient.id)
    await login(client, "doctor@example.com")

    answers = []
    for alert_id in (foreign.id, uuid4()):
        delivered = await client.post(f"/api/v1/alerts/{alert_id}/delivered?via=ws")
        acknowledged = await client.post(f"/api/v1/alerts/{alert_id}/acknowledge")
        answers.append([(r.status_code, r.json()) for r in (delivered, acknowledged)])

    assert answers[0] == answers[1]
    assert [status for status, _ in answers[0]] == [404, 404]


async def test_patient_alerts_include_history(
    client: httpx.AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    rules: RuleBook,
    care: Care,
) -> None:
    alert = await fire(sessionmaker, rules, care.patient.id)
    await fire(sessionmaker, rules, care.patient.id)
    await login(client, "doctor@example.com")

    response = await client.get(f"/api/v1/patients/{care.patient.id}/alerts")

    assert response.status_code == 200
    [listed] = response.json()
    assert listed["id"] == str(alert.id)
    assert listed["occurrences"] == 2
    assert len(listed["triggers"]) == 2
    assert await access_log(sessionmaker) == [("patient.alerts.list", care.patient.id, None)]


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/v1/alerts?status=open"),
        ("GET", "/api/v1/patients/{patient}/alerts"),
        ("POST", "/api/v1/alerts/{alert}/delivered?via=ws"),
        ("POST", "/api/v1/alerts/{alert}/acknowledge"),
        ("POST", "/api/v1/alerts/{alert}/no-contact"),
        ("GET", "/api/v1/alerts/decisions"),
    ],
)
async def test_patient_cannot_use_alerts_api(
    client: httpx.AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    rules: RuleBook,
    care: Care,
    method: str,
    path: str,
) -> None:
    alert = await fire(sessionmaker, rules, care.patient.id)
    await login(client, "patient@example.com")

    response = await client.request(method, path.format(patient=care.patient.id, alert=alert.id))

    assert response.status_code == 403
    [unchanged] = await patient_alerts(sessionmaker, care.patient.id)
    assert (unchanged.delivered_at, unchanged.acknowledged_at) == (None, None)
