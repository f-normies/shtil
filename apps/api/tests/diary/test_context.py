"""Поведение контекста ежедневного дневника."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

import httpx

from tests.diary.conftest import DiaryCare, login


async def test_context_reports_server_day_in_configured_timezone(
    client: httpx.AsyncClient,
    diary_care: DiaryCare,
) -> None:
    await login(client, diary_care.patient.email)

    response = await client.get("/api/v1/diary/context")

    assert response.status_code == 200
    context = response.json()
    server_now = datetime.fromisoformat(context["server_now"])
    assert context["timezone"] == "Europe/Moscow"
    assert context["today"] == server_now.astimezone(ZoneInfo("Europe/Moscow")).date().isoformat()
    assert context["daily_status"] == "missing"
    assert context["month_entry_count"] == 0
    assert (
        date.fromisoformat(context["observation_from"])
        <= server_now.astimezone(ZoneInfo("Europe/Moscow")).date()
    )
