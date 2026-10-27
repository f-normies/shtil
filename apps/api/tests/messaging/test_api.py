"""Текстовые HTTP-контракты сообщений, истории и прочтения."""

from uuid import UUID, uuid4

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.modules.freetext.models import Analysis
from clin_compass.modules.identity.models import AccessLogEntry
from tests.conftest import MakeUser
from tests.messaging.conftest import MakeMessagingCare, login


def _message_url(patient_id: str) -> str:
    return f"/api/v1/patients/{patient_id}/messages"


async def test_patient_text_is_saved_once_and_idempotent_replay_returns_same_message(
    client: httpx.AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    make_messaging_care: MakeMessagingCare,
) -> None:
    care = await make_messaging_care("message-doctor@example.com", "message-patient@example.com")
    await login(client, care.patient.email)
    client_message_id = uuid4()
    body = {
        "expected_doctor_id": str(care.doctor.id),
        "client_message_id": str(client_message_id),
        "text": "Сегодня настроение стало лучше.",
    }

    created = await client.post(_message_url(str(care.patient.id)), json=body)

    assert created.status_code == 201
    assert created.headers["cache-control"] == "no-store"
    original = created.json()
    assert original["sender_id"] == str(care.patient.id)
    assert original["client_message_id"] == str(client_message_id)
    assert original["kind"] == "text"
    assert original["text"] == body["text"]
    assert original["seq"] == 1
    assert original["analysis_status"] == "processing"

    replay = await client.post(_message_url(str(care.patient.id)), json=body)

    assert replay.status_code == 200
    assert replay.json()["id"] == original["id"]
    assert replay.json()["seq"] == original["seq"]
    async with sessionmaker() as session:
        analyses = list(
            await session.scalars(
                select(Analysis).where(
                    Analysis.source_type == "message",
                    Analysis.source_id == UUID(original["id"]),
                )
            )
        )
    assert len(analyses) == 1
    assert analyses[0].patient_id == care.patient.id
    assert analyses[0].text == body["text"]
    assert analyses[0].transcript is False


async def test_history_does_not_mark_incoming_message_read(
    client: httpx.AsyncClient,
    make_messaging_care: MakeMessagingCare,
) -> None:
    care = await make_messaging_care("history-doctor@example.com", "history-patient@example.com")
    await login(client, care.patient.email)
    sent = await client.post(
        _message_url(str(care.patient.id)),
        json={
            "expected_doctor_id": str(care.doctor.id),
            "client_message_id": str(uuid4()),
            "text": "Короткое сообщение для истории.",
        },
    )
    assert sent.status_code == 201

    await login(client, care.doctor.email)
    history_url = _message_url(str(care.patient.id))
    history = await client.get(history_url)
    unread = await client.get("/api/v1/messaging/unread")
    history_again = await client.get(history_url)

    assert history.status_code == 200
    assert history.headers["cache-control"] == "no-store"
    assert history.json()["doctor_id"] == str(care.doctor.id)
    assert history.json()["last_seq"] == 1
    assert history.json()["read_through_seq"] == 0
    assert history.json()["unread_count"] == 1
    assert history.json()["items"][0]["id"] == sent.json()["id"]
    assert unread.status_code == 200
    assert unread.headers["cache-control"] == "no-store"
    assert unread.json() == [
        {
            "patient_id": str(care.patient.id),
            "doctor_id": str(care.doctor.id),
            "unread_count": 1,
            "last_message_id": sent.json()["id"],
            "last_message_at": sent.json()["created_at"],
        }
    ]
    assert history_again.json()["read_through_seq"] == 0
    assert history_again.json()["unread_count"] == 1

    reply = await client.post(
        history_url,
        json={
            "expected_doctor_id": str(care.doctor.id),
            "client_message_id": str(uuid4()),
            "text": "Ответ врача пациенту.",
        },
    )
    assert reply.status_code == 201
    await login(client, care.patient.email)
    patient_unread = await client.get("/api/v1/messaging/unread")
    assert patient_unread.status_code == 200
    assert patient_unread.headers["cache-control"] == "no-store"
    assert patient_unread.json() == [
        {
            "patient_id": str(care.patient.id),
            "doctor_id": str(care.doctor.id),
            "unread_count": 1,
            "last_message_id": reply.json()["id"],
            "last_message_at": reply.json()["created_at"],
        }
    ]


async def test_read_cursor_only_moves_forward_and_rejects_seq_after_conversation_end(
    client: httpx.AsyncClient,
    make_messaging_care: MakeMessagingCare,
) -> None:
    care = await make_messaging_care("read-doctor@example.com", "read-patient@example.com")
    await login(client, care.patient.email)
    sent = await client.post(
        _message_url(str(care.patient.id)),
        json={
            "expected_doctor_id": str(care.doctor.id),
            "client_message_id": str(uuid4()),
            "text": "Сообщение для подтверждения прочтения.",
        },
    )
    assert sent.status_code == 201
    await login(client, care.doctor.email)

    read_url = f"{_message_url(str(care.patient.id))}/read"
    first = await client.post(
        read_url,
        json={"expected_doctor_id": str(care.doctor.id), "through_seq": 1},
    )
    older = await client.post(
        read_url,
        json={"expected_doctor_id": str(care.doctor.id), "through_seq": 0},
    )
    future = await client.post(
        read_url,
        json={"expected_doctor_id": str(care.doctor.id), "through_seq": 2},
    )

    assert first.status_code == 200
    assert first.headers["cache-control"] == "no-store"
    assert first.json()["read_through_seq"] == 1
    assert first.json()["unread_count"] == 0
    assert older.status_code == 200
    assert older.json()["read_through_seq"] == 1
    assert future.status_code == 422
    assert future.json()["code"] == "invalid_cursor"


async def test_reusing_client_message_id_with_different_text_returns_idempotency_conflict(
    client: httpx.AsyncClient,
    make_messaging_care: MakeMessagingCare,
) -> None:
    care = await make_messaging_care("conflict-doctor@example.com", "conflict-patient@example.com")
    await login(client, care.patient.email)
    url = _message_url(str(care.patient.id))
    body = {
        "expected_doctor_id": str(care.doctor.id),
        "client_message_id": str(uuid4()),
        "text": "Первое содержимое ключа.",
    }
    created = await client.post(url, json=body)
    changed = await client.post(url, json={**body, "text": "Другое содержимое ключа."})

    assert created.status_code == 201
    assert changed.status_code == 409
    assert changed.json()["code"] == "idempotency_conflict"


async def test_patient_send_refuses_a_stale_expected_doctor_id(
    client: httpx.AsyncClient,
    make_messaging_care: MakeMessagingCare,
    make_user: MakeUser,
) -> None:
    care = await make_messaging_care(
        "stale-send-doctor@example.com", "stale-send-patient@example.com"
    )
    other_doctor = await make_user(email="stale-send-other@example.com")
    await login(client, care.patient.email)

    response = await client.post(
        _message_url(str(care.patient.id)),
        json={
            "expected_doctor_id": str(other_doctor.id),
            "client_message_id": str(uuid4()),
            "text": "Отложенная отправка в прежнюю пару.",
        },
    )

    assert response.status_code == 409
    assert response.json()["code"] == "conversation_changed"


async def test_text_must_be_nonblank_and_at_most_twenty_thousand_characters(
    client: httpx.AsyncClient,
    make_messaging_care: MakeMessagingCare,
) -> None:
    care = await make_messaging_care(
        "validation-doctor@example.com", "validation-patient@example.com"
    )
    await login(client, care.patient.email)
    url = _message_url(str(care.patient.id))
    prefix = {"expected_doctor_id": str(care.doctor.id), "client_message_id": str(uuid4())}

    blank = await client.post(url, json={**prefix, "text": " \n\t "})
    too_long = await client.post(url, json={**prefix, "text": "а" * 20_001})

    assert blank.status_code == 422
    assert blank.headers["content-type"].startswith("application/problem+json")
    assert too_long.status_code == 422
    assert too_long.headers["content-type"].startswith("application/problem+json")


async def test_doctor_message_is_not_submitted_to_freetext(
    client: httpx.AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    make_messaging_care: MakeMessagingCare,
) -> None:
    care = await make_messaging_care(
        "doctor-send-doctor@example.com", "doctor-send-patient@example.com"
    )
    await login(client, care.doctor.email)

    response = await client.post(
        _message_url(str(care.patient.id)),
        json={
            "expected_doctor_id": str(care.doctor.id),
            "client_message_id": str(uuid4()),
            "text": "Запись врача после приёма.",
        },
    )

    assert response.status_code == 201
    assert response.json()["analysis_status"] is None
    assert response.json()["analysis_skip_reason"] == "doctor_message"
    async with sessionmaker() as session:
        analyses = list(await session.scalars(select(Analysis)))
    assert analyses == []


async def test_foreign_doctor_cannot_read_patient_history_and_stale_pair_cursor_is_rejected(
    client: httpx.AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    make_user: MakeUser,
    make_messaging_care: MakeMessagingCare,
) -> None:
    care = await make_messaging_care("owner-doctor@example.com", "owned-patient@example.com")
    other_doctor = await make_user(email="other-doctor@example.com")
    await login(client, other_doctor.email)

    foreign_history = await client.get(_message_url(str(care.patient.id)))

    foreign_care = await make_messaging_care(
        "foreign-patient-doctor@example.com", "foreign-patient@example.com"
    )
    await login(client, care.patient.email)
    foreign_patient_history = await client.get(_message_url(str(foreign_care.patient.id)))

    assert foreign_history.status_code == 404
    assert foreign_patient_history.status_code == 404

    stale = await client.get(
        _message_url(str(care.patient.id)),
        params={"after_seq": 0, "expected_doctor_id": str(other_doctor.id)},
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "conversation_changed"
    async with sessionmaker() as session:
        denied = list(
            (
                await session.execute(
                    select(AccessLogEntry.actor_id, AccessLogEntry.patient_id).where(
                        AccessLogEntry.action == "messaging.history.denied"
                    )
                )
            ).all()
        )
    assert set(denied) == {
        (other_doctor.id, care.patient.id),
        (care.patient.id, care.patient.id),
        (care.patient.id, foreign_care.patient.id),
    }


async def test_empty_history_has_current_pair_context_and_cursors_are_validated(
    client: httpx.AsyncClient,
    make_messaging_care: MakeMessagingCare,
) -> None:
    care = await make_messaging_care("empty-doctor@example.com", "empty-patient@example.com")
    await login(client, care.patient.email)

    empty = await client.get(_message_url(str(care.patient.id)))
    missing_pair = await client.get(_message_url(str(care.patient.id)), params={"after_seq": 0})
    both_cursors = await client.get(
        _message_url(str(care.patient.id)),
        params={
            "before_seq": 1,
            "after_seq": 0,
            "expected_doctor_id": str(care.doctor.id),
        },
    )

    assert empty.status_code == 200
    assert empty.json() == {
        "items": [],
        "doctor_id": str(care.doctor.id),
        "last_seq": 0,
        "read_through_seq": 0,
        "peer_read_through_seq": 0,
        "unread_count": 0,
        "next_before_seq": None,
        "next_after_seq": None,
    }
    assert missing_pair.status_code == 422
    assert missing_pair.json()["code"] == "invalid_cursor"
    assert both_cursors.status_code == 422
    assert both_cursors.json()["code"] == "invalid_cursor"


async def test_history_limit_is_between_one_and_one_hundred(
    client: httpx.AsyncClient,
    make_messaging_care: MakeMessagingCare,
) -> None:
    care = await make_messaging_care("limit-doctor@example.com", "limit-patient@example.com")
    await login(client, care.patient.email)

    zero = await client.get(_message_url(str(care.patient.id)), params={"limit": 0})
    too_many = await client.get(_message_url(str(care.patient.id)), params={"limit": 101})

    assert zero.status_code == 422
    assert zero.headers["content-type"].startswith("application/problem+json")
    assert too_many.status_code == 422
    assert too_many.headers["content-type"].startswith("application/problem+json")
