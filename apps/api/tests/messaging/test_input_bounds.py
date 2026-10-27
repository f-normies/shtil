"""Входные данные, которые Postgres не примет, отклоняются валидацией до обращения к БД."""

from uuid import uuid4

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.modules.identity.models import AccessLogEntry
from clin_compass.modules.messaging.models import Message
from tests.messaging.conftest import MakeMessagingCare, login

# Номер сообщения хранится в Postgres integer: больше 2**31 - 1 asyncpg не передаст.
INT4_MAX = 2**31 - 1


def _message_url(patient_id: str) -> str:
    return f"/api/v1/patients/{patient_id}/messages"


async def test_text_with_nul_character_is_rejected_as_validation_error(
    client: httpx.AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    make_messaging_care: MakeMessagingCare,
) -> None:
    care = await make_messaging_care("nul-doctor@example.com", "nul-patient@example.com")
    await login(client, care.patient.email)

    response = await client.post(
        _message_url(str(care.patient.id)),
        json={
            "expected_doctor_id": str(care.doctor.id),
            "client_message_id": str(uuid4()),
            "text": "до\x00после",
        },
    )

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")
    assert "\x00" not in response.text
    async with sessionmaker() as session:
        stored = await session.scalar(select(func.count(Message.id)))
        denied = await session.scalar(
            select(func.count(AccessLogEntry.id)).where(
                AccessLogEntry.action == "messaging.send.denied"
            )
        )
    assert stored == 0
    assert denied == 0


async def test_history_cursors_above_postgres_integer_are_rejected(
    client: httpx.AsyncClient,
    make_messaging_care: MakeMessagingCare,
) -> None:
    care = await make_messaging_care("cursor-doctor@example.com", "cursor-patient@example.com")
    await login(client, care.patient.email)
    url = _message_url(str(care.patient.id))
    pair = {"expected_doctor_id": str(care.doctor.id)}
    # Курсор попадает в SQL, только когда диалог уже есть.
    sent = await client.post(
        url, json={**pair, "client_message_id": str(uuid4()), "text": "Первое сообщение."}
    )
    assert sent.status_code == 201

    before = await client.get(url, params={**pair, "before_seq": INT4_MAX + 1})
    after = await client.get(url, params={**pair, "after_seq": INT4_MAX + 1})
    largest = await client.get(url, params={**pair, "before_seq": INT4_MAX})

    assert before.status_code == 422
    assert before.headers["content-type"].startswith("application/problem+json")
    assert after.status_code == 422
    assert after.headers["content-type"].startswith("application/problem+json")
    assert largest.status_code == 200
    assert [item["id"] for item in largest.json()["items"]] == [sent.json()["id"]]


async def test_read_cursor_above_postgres_integer_is_rejected(
    client: httpx.AsyncClient,
    make_messaging_care: MakeMessagingCare,
) -> None:
    care = await make_messaging_care("read-max-doctor@example.com", "read-max-patient@example.com")
    await login(client, care.patient.email)

    response = await client.post(
        f"{_message_url(str(care.patient.id))}/read",
        json={"expected_doctor_id": str(care.doctor.id), "through_seq": INT4_MAX + 1},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_cursor"
