"""Порядок нумерации, пагинация и атомарность событий messaging."""

import asyncio
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.core.tokens import token_hash
from clin_compass.modules.freetext.models import Analysis
from clin_compass.modules.messaging import service as messaging
from clin_compass.modules.messaging.models import Message
from clin_compass.modules.patients.models import Patient
from tests.conftest import MakeUser, listener_count
from tests.messaging.conftest import MakeMessagingCare, login


class RecordingSocket:
    def __init__(self) -> None:
        self.events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    async def send_json(self, data: Any) -> None:
        await self.events.put(data)

    async def close(self, code: int = 1000) -> None:
        del code


def _url(patient_id: str) -> str:
    return f"/api/v1/patients/{patient_id}/messages"


async def test_history_pages_by_sequence_and_exposes_both_directions(
    client: httpx.AsyncClient,
    make_messaging_care: MakeMessagingCare,
) -> None:
    care = await make_messaging_care("pages-doctor@example.com", "pages-patient@example.com")
    await login(client, care.patient.email)
    for index in range(3):
        response = await client.post(
            _url(str(care.patient.id)),
            json={
                "expected_doctor_id": str(care.doctor.id),
                "client_message_id": str(uuid4()),
                "text": f"Сообщение {index + 1}.",
            },
        )
        assert response.status_code == 201

    await login(client, care.doctor.email)
    url = _url(str(care.patient.id))
    latest = await client.get(url, params={"limit": 2})
    earlier = await client.get(
        url,
        params={"before_seq": 2, "expected_doctor_id": str(care.doctor.id), "limit": 2},
    )
    after_first = await client.get(
        url,
        params={"after_seq": 1, "expected_doctor_id": str(care.doctor.id), "limit": 1},
    )

    assert latest.status_code == 200
    assert latest.headers["cache-control"] == "no-store"
    assert [item["seq"] for item in latest.json()["items"]] == [2, 3]
    assert latest.json()["next_before_seq"] == 2
    assert latest.json()["next_after_seq"] is None
    assert [item["seq"] for item in earlier.json()["items"]] == [1]
    assert earlier.json()["next_before_seq"] is None
    assert earlier.json()["next_after_seq"] == 1
    assert [item["seq"] for item in after_first.json()["items"]] == [2]
    assert after_first.json()["next_after_seq"] == 2


async def test_parallel_patient_sends_allocate_distinct_consecutive_sequences(
    client: httpx.AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    make_messaging_care: MakeMessagingCare,
) -> None:
    care = await make_messaging_care("parallel-doctor@example.com", "parallel-patient@example.com")
    await login(client, care.patient.email)

    responses = await asyncio.gather(
        *(
            client.post(
                _url(str(care.patient.id)),
                json={
                    "expected_doctor_id": str(care.doctor.id),
                    "client_message_id": str(uuid4()),
                    "text": f"Параллельное сообщение {index}.",
                },
            )
            for index in range(2)
        )
    )

    assert [response.status_code for response in responses] == [201, 201]
    assert sorted(response.json()["seq"] for response in responses) == [1, 2]
    async with sessionmaker() as session:
        rows = list(
            (
                await session.scalars(
                    select(Message)
                    .where(Message.sender_id == care.patient.id)
                    .order_by(Message.seq)
                )
            ).all()
        )
    assert [message.seq for message in rows] == [1, 2]


async def test_publish_failure_rolls_back_message_analysis_and_notification(
    client: httpx.AsyncClient,
    app: FastAPI,
    sessionmaker: async_sessionmaker[AsyncSession],
    make_messaging_care: MakeMessagingCare,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    care = await make_messaging_care("rollback-doctor@example.com", "rollback-patient@example.com")
    await login(client, care.patient.email)
    await login(client, care.doctor.email)
    doctor_token = client.cookies.get("compass_session")
    assert doctor_token is not None
    receiver = RecordingSocket()
    app.state.hub.connect(care.doctor.id, receiver, session=token_hash(doctor_token))
    await login(client, care.patient.email)
    for _ in range(100):
        if await listener_count(sessionmaker):
            break
        await asyncio.sleep(0.02)
    else:
        pytest.fail("realtime listener did not connect")

    success = await client.post(
        _url(str(care.patient.id)),
        json={
            "expected_doctor_id": str(care.doctor.id),
            "client_message_id": str(uuid4()),
            "text": "Текст успешного сообщения.",
        },
    )
    assert success.status_code == 201
    event = await asyncio.wait_for(receiver.events.get(), timeout=3)
    assert event["type"] == "message.created"
    assert set(event["data"]) == {"patient_id", "doctor_id", "message_id", "seq"}
    assert "Текст успешного сообщения." not in str(event)

    await login(client, care.doctor.email)
    read_url = f"{_url(str(care.patient.id))}/read"
    marked = await client.post(
        read_url,
        json={"expected_doctor_id": str(care.doctor.id), "through_seq": 1},
    )
    read_event = await asyncio.wait_for(receiver.events.get(), timeout=3)
    repeated = await client.post(
        read_url,
        json={"expected_doctor_id": str(care.doctor.id), "through_seq": 1},
    )
    await asyncio.sleep(0.1)
    assert marked.status_code == 200
    assert read_event["type"] == "message.read"
    assert set(read_event["data"]) == {
        "patient_id",
        "doctor_id",
        "reader_id",
        "through_seq",
    }
    assert read_event["data"]["reader_id"] == str(care.doctor.id)
    assert repeated.status_code == 200
    assert receiver.events.empty()

    original_publish = messaging.realtime.publish
    await login(client, care.patient.email)

    async def publish_then_fail(*args: Any, **kwargs: Any) -> None:
        await original_publish(*args, **kwargs)
        raise SQLAlchemyError("sensitive message body")

    monkeypatch.setattr(messaging.realtime, "publish", publish_then_fail)
    failed = await client.post(
        _url(str(care.patient.id)),
        json={
            "expected_doctor_id": str(care.doctor.id),
            "client_message_id": str(uuid4()),
            "text": "Текст, который должен откатиться.",
        },
    )
    await asyncio.sleep(0.1)

    assert failed.status_code == 503
    assert "sensitive message body" not in failed.text
    assert receiver.events.empty()
    async with sessionmaker() as session:
        messages = list((await session.scalars(select(Message))).all())
        analyses = list((await session.scalars(select(Analysis))).all())
    assert len(messages) == 1
    assert len(analyses) == 1


async def test_client_message_id_from_previous_pair_is_not_replayed_into_current_pair(
    client: httpx.AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    make_messaging_care: MakeMessagingCare,
    make_user: MakeUser,
) -> None:
    care = await make_messaging_care("pair-old-doctor@example.com", "pair-patient@example.com")
    await login(client, care.patient.email)
    client_message_id = uuid4()
    first = await client.post(
        _url(str(care.patient.id)),
        json={
            "expected_doctor_id": str(care.doctor.id),
            "client_message_id": str(client_message_id),
            "text": "Сообщение прежней пары.",
        },
    )
    new_doctor = await make_user(email="pair-new-doctor@example.com")
    async with sessionmaker() as session:
        patient = await session.get(Patient, care.patient.id)
        assert patient is not None
        patient.lead_doctor_id = new_doctor.id
        await session.commit()

    replay = await client.post(
        _url(str(care.patient.id)),
        json={
            "expected_doctor_id": str(new_doctor.id),
            "client_message_id": str(client_message_id),
            "text": "Сообщение прежней пары.",
        },
    )

    assert first.status_code == 201
    assert replay.status_code == 409
    assert replay.json()["code"] == "conversation_changed"
    async with sessionmaker() as session:
        rows = list(
            (
                await session.scalars(
                    select(Message).where(
                        Message.sender_id == care.patient.id,
                        Message.client_message_id == client_message_id,
                    )
                )
            ).all()
        )
    assert len(rows) == 1


async def test_doctor_unread_drops_patient_reassigned_after_patient_list_read(
    client: httpx.AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    make_messaging_care: MakeMessagingCare,
    make_user: MakeUser,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    care = await make_messaging_care("unread-old-doctor@example.com", "unread-patient@example.com")
    await login(client, care.patient.email)
    sent = await client.post(
        _url(str(care.patient.id)),
        json={
            "expected_doctor_id": str(care.doctor.id),
            "client_message_id": str(uuid4()),
            "text": "Непрочитанное сообщение прежнему врачу.",
        },
    )
    assert sent.status_code == 201

    await login(client, care.doctor.email)
    before_change = await client.get("/api/v1/messaging/unread")
    assert before_change.status_code == 200
    assert before_change.json()[0]["last_message_id"] == sent.json()["id"]

    new_doctor = await make_user(email="unread-new-doctor@example.com")
    patient_list_read = asyncio.Event()
    continue_read = asyncio.Event()
    original_list_patients = messaging.patients.list_patients

    async def list_then_pause(session: AsyncSession, doctor_id: UUID) -> list[Any]:
        result = await original_list_patients(session, doctor_id)
        patient_list_read.set()
        await continue_read.wait()
        return result

    monkeypatch.setattr(messaging.patients, "list_patients", list_then_pause)
    pending_request = asyncio.create_task(client.get("/api/v1/messaging/unread"))
    try:
        await asyncio.wait_for(patient_list_read.wait(), timeout=3)
        async with sessionmaker() as session:
            patient = await session.get(Patient, care.patient.id)
            assert patient is not None
            patient.lead_doctor_id = new_doctor.id
            await session.commit()
    finally:
        continue_read.set()
    after_change = await pending_request

    assert after_change.status_code == 200
    assert after_change.json() == []
