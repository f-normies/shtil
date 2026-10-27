"""Отметки доз и приём за 14 дней на настоящей БД (спека #34, раздел 9).

Назначение — `test-drug` из `tests/therapy/knowledge`, 2 раза в сутки. Дни — по Москве.
Время API фиксирует `clock`: доля и `due` зависят от часа (ревью PR #158).
"""

from collections.abc import Callable
from datetime import date, datetime, time, timedelta

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.modules.adherence import service
from clin_compass.modules.adherence.doses import MOSCOW, slot_times
from clin_compass.modules.identity.schemas import Role
from tests.conftest import MakeUser
from tests.patients.test_access import login
from tests.therapy.conftest import DOCTOR, PATIENT, Care
from tests.therapy.test_api import prescriptions, ready

Clock = Callable[[int], date]


def moscow_today() -> date:
    return datetime.now(MOSCOW).date()


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    """`clock(14)` — API видит сегодня 14:00 по Москве; возвращает сегодняшний день."""

    def set_hour(hour: int) -> date:
        moment = datetime.combine(moscow_today(), time(hour, 0), MOSCOW)
        monkeypatch.setattr(service, "now", lambda: moment)
        return moment.date()

    return set_hour


async def prescribe(
    care: Care,
    sessionmaker: async_sessionmaker[AsyncSession],
    days_ago: int = 20,
    started_at: datetime | None = None,
) -> str:
    """Врач подтвердил назначение `days_ago` дней назад или в `started_at`; дальше вошёл пациент."""
    option = await ready(care)
    await care.client.post(f"/api/v1/proposals/{option['proposal']['id']}/confirm", json={})
    [prescription] = await prescriptions(care)
    async with sessionmaker() as session:
        if started_at is None:
            await session.execute(
                text(
                    "UPDATE therapy.prescriptions"
                    " SET started_at = started_at - make_interval(days => :d)"
                ),
                {"d": days_ago},
            )
        else:
            await session.execute(
                text("UPDATE therapy.prescriptions SET started_at = :at"), {"at": started_at}
            )
        await session.commit()
    care.client.cookies.clear()
    await login(care.client, PATIENT)
    return str(prescription["id"])


async def put(
    client: httpx.AsyncClient, pid: str, day: date, slot: int, status: str | None
) -> httpx.Response:
    return await client.put(
        "/api/v1/me/doses",
        json={
            "marks": [
                {"prescription_id": pid, "day": day.isoformat(), "slot": slot, "status": status}
            ]
        },
    )


async def dose_rows(sessionmaker: async_sessionmaker[AsyncSession]) -> list[str]:
    async with sessionmaker() as session:
        rows = await session.scalars(
            text("SELECT status FROM adherence.dose_marks ORDER BY marked_at, id")
        )
        return list(rows)


async def test_patient_sees_todays_doses_with_slot_labels(
    care: Care, sessionmaker: async_sessionmaker[AsyncSession], clock: Clock
) -> None:
    await prescribe(care, sessionmaker)
    today = clock(14)

    body = (await care.client.get("/api/v1/me/doses")).json()

    assert body["day"] == today.isoformat()
    # В 14:00 время первого приёма пришло, второго — нет: «Готово» его не тронет.
    assert [(d["slot"], d["status"], d["due"]) for d in body["doses"]] == [
        (0, None, True),
        (1, None, False),
    ]
    assert all(d["slot_label"] for d in body["doses"])


async def test_start_day_offers_only_doses_after_the_start(
    care: Care, sessionmaker: async_sessionmaker[AsyncSession], clock: Clock
) -> None:
    # Ревью PR #158: назначение подтверждено вечером — первого приёма в этот день нет.
    first, second = slot_times(2, "утром и вечером")
    between = datetime.combine(moscow_today(), first, MOSCOW) + timedelta(hours=1)
    assert between.time() < second
    pid = await prescribe(care, sessionmaker, started_at=between)
    today = clock(23)

    body = (await care.client.get("/api/v1/me/doses")).json()
    first_slot = await put(care.client, pid, today, 0, "missed")

    assert [d["slot"] for d in body["doses"]] == [1]
    assert first_slot.status_code == 422
    assert await dose_rows(sessionmaker) == []


async def test_mark_clear_and_repeat_keep_history(
    care: Care, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    pid = await prescribe(care, sessionmaker)
    today = moscow_today()

    taken = await put(care.client, pid, today, 0, "taken")
    again = await put(care.client, pid, today, 0, "taken")
    cleared = await put(care.client, pid, today, 0, None)

    assert taken.status_code == again.status_code == cleared.status_code == 200
    assert taken.json()["doses"][0]["status"] == "taken"
    assert cleared.json()["doses"][0]["status"] is None
    # Повтор того же статуса новой строки не пишет; снятие — новая строка, не удаление.
    assert await dose_rows(sessionmaker) == ["taken", "cleared"]


async def test_invalid_doses_are_rejected_and_nothing_is_written(
    care: Care, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    pid = await prescribe(care, sessionmaker, days_ago=3)
    today = moscow_today()

    statuses = [
        (await put(care.client, pid, today + timedelta(days=1), 0, "taken")).status_code,
        (await put(care.client, pid, today - timedelta(days=8), 0, "taken")).status_code,
        (await put(care.client, pid, today - timedelta(days=4), 0, "taken")).status_code,
        (await put(care.client, pid, today, 2, "taken")).status_code,
    ]
    mixed = await care.client.put(
        "/api/v1/me/doses",
        json={
            "marks": [
                {"prescription_id": pid, "day": today.isoformat(), "slot": 0, "status": "taken"},
                {"prescription_id": pid, "day": today.isoformat(), "slot": 5, "status": "taken"},
            ]
        },
    )

    assert statuses == [422, 422, 422, 422]
    assert mixed.status_code == 422
    assert await dose_rows(sessionmaker) == []


async def test_other_patients_prescription_is_not_found(
    care: Care, sessionmaker: async_sessionmaker[AsyncSession], make_user: MakeUser
) -> None:
    pid = await prescribe(care, sessionmaker)
    await make_user(email="other-patient@example.com", role=Role.PATIENT)
    care.client.cookies.clear()
    await login(care.client, "other-patient@example.com")

    response = await put(care.client, pid, moscow_today(), 0, "taken")

    assert response.status_code == 404


async def test_doctor_sees_adherence_from_patient_marks(
    care: Care, sessionmaker: async_sessionmaker[AsyncSession], clock: Clock
) -> None:
    pid = await prescribe(care, sessionmaker)
    today = clock(14)
    marks = [
        {
            "prescription_id": pid,
            "day": (today - timedelta(days=i)).isoformat(),
            "slot": s,
            "status": "taken",
        }
        for i in range(1, 4)
        for s in (0, 1)
    ]
    marks[1]["status"] = "missed"
    assert (await care.client.put("/api/v1/me/doses", json={"marks": marks})).status_code == 200
    care.client.cookies.clear()
    await login(care.client, DOCTOR)

    body = (await care.client.get(f"{care.base}/adherence")).json()

    assert body["course"] == "active"
    assert body["period"] == {
        "start": (today - timedelta(days=13)).isoformat(),
        "end": today.isoformat(),
    }
    # Сегодня в 14:00 второй приём ещё впереди: он не входит в знаменатель (ревью PR #158).
    assert (body["planned"], body["taken"], body["missed"], body["unmarked"]) == (27, 5, 1, 21)
    assert body["marked_any"] is True
    assert [p["id"] for p in body["prescriptions"]] == [pid]


async def test_adherence_without_prescriptions_is_none(care: Care) -> None:
    body = (await care.client.get(f"{care.base}/adherence")).json()

    assert (body["course"], body["period"], body["days"]) == ("none", None, [])


async def test_other_doctor_gets_404_and_access_is_logged(
    care: Care, sessionmaker: async_sessionmaker[AsyncSession], make_user: MakeUser
) -> None:
    await care.client.get(f"{care.base}/adherence")
    await make_user(email="other@example.com")
    care.client.cookies.clear()
    await login(care.client, "other@example.com")

    response = await care.client.get(f"{care.base}/adherence")

    assert response.status_code == 404
    async with sessionmaker() as session:
        actions = set((await session.scalars(text("SELECT action FROM identity.access_log"))).all())
    assert {"patient.adherence.view", "patient.adherence.view.denied"} <= actions
