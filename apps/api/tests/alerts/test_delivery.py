"""Путь алерта целиком (спека фундамента 4.5, метрика 1): данные → сигнал → WebSocket → доставлен.

Настоящий uvicorn, настоящий WebSocket-клиент, правила из tests/alerts/knowledge.
"""

import asyncio
from datetime import datetime

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from websockets.asyncio.client import ClientConnection

from clin_compass.modules.alerts import service as alerts
from clin_compass.modules.alerts.rules import RuleBook
from tests.alerts.conftest import SELF_HARM, Care, make_care
from tests.live import receive, session_cookie, ws_connect


async def test_critical_answer_reaches_lead_doctor_within_a_second(
    live_server: str,
    sessionmaker: async_sessionmaker[AsyncSession],
    rules: RuleBook,
    care: Care,
) -> None:
    await make_care(sessionmaker, "other-doctor@example.com", "other@example.com")
    doctor = await session_cookie(live_server, "doctor@example.com")
    other = await session_cookie(live_server, "other-doctor@example.com")

    async with (
        ws_connect(live_server, doctor) as doctor_ws,
        ws_connect(live_server, other) as other_ws,
    ):
        await receive(doctor_ws)  # ready
        await receive(other_ws)  # ready
        async with sessionmaker() as session:
            await alerts.check_results(
                session,
                rules,
                patient_id=care.patient.id,
                submitted_scale="test-mood",
                results=SELF_HARM,
                origin_ref="s:1",
            )
            await session.commit()
        message = await receive_event_type(doctor_ws, "alert.created", max_wait_seconds=1)
        alert_id = message["data"]["alert_id"]  # type: ignore[index]
        async with httpx.AsyncClient(
            base_url=f"http://{live_server}", headers={"Cookie": doctor}
        ) as client:
            delivered = await client.post(f"/api/v1/alerts/{alert_id}/delivered?via=ws")
            [alert] = (await client.get("/api/v1/alerts?status=open")).json()
        with pytest.raises(TimeoutError):
            await receive_event_type(other_ws, "alert.created", max_wait_seconds=0.3)

    assert message["type"] == "alert.created"
    assert delivered.status_code == 204
    latency = datetime.fromisoformat(alert["delivered_at"]) - datetime.fromisoformat(
        alert["created_at"]
    )
    assert latency.total_seconds() < 1


async def receive_event_type(
    ws: ClientConnection, event_type: str, *, max_wait_seconds: float
) -> dict[str, object]:
    async def read_until_match() -> dict[str, object]:
        while True:
            event = await receive(ws)
            if event.get("type") == event_type:
                return event

    return await asyncio.wait_for(read_until_match(), timeout=max_wait_seconds)
