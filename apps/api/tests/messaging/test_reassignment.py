"""Смена ведущего врача через REST: доступ только у текущей пары, история пар сохраняется."""

from typing import Any
from uuid import UUID, uuid4

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.modules.identity.schemas import UserOut
from clin_compass.modules.patients.models import Patient
from tests.conftest import MakeUser
from tests.messaging.conftest import MakeMessagingCare, login


def _message_url(patient_id: UUID) -> str:
    return f"/api/v1/patients/{patient_id}/messages"


async def _reassign(
    sessionmaker: async_sessionmaker[AsyncSession], patient_id: UUID, doctor_id: UUID
) -> None:
    # API смены врача в #33 нет: меняем связь напрямую, как это сделает будущая операция.
    async with sessionmaker() as session:
        patient = await session.get(Patient, patient_id)
        assert patient is not None
        patient.lead_doctor_id = doctor_id
        await session.commit()


async def _send(
    client: httpx.AsyncClient, patient_id: UUID, doctor_id: UUID, text: str
) -> httpx.Response:
    return await client.post(
        _message_url(patient_id),
        json={
            "expected_doctor_id": str(doctor_id),
            "client_message_id": str(uuid4()),
            "text": text,
        },
    )


async def _history(client: httpx.AsyncClient, patient_id: UUID) -> dict[str, Any]:
    response = await client.get(_message_url(patient_id))
    assert response.status_code == 200
    body: dict[str, Any] = response.json()
    return body


def _ids(history: dict[str, Any]) -> list[str]:
    return [item["id"] for item in history["items"]]


async def _assert_former_doctor_is_locked_out(
    client: httpx.AsyncClient, doctor: UserOut, patient_id: UUID
) -> None:
    await login(client, doctor.email)
    history = await client.get(_message_url(patient_id))
    send = await _send(client, patient_id, doctor.id, "Отложенная отправка прежнему врачу.")
    read = await client.post(
        f"{_message_url(patient_id)}/read",
        json={"expected_doctor_id": str(doctor.id), "through_seq": 1},
    )
    assert [history.status_code, send.status_code, read.status_code] == [404, 404, 404]


async def test_reassignment_moves_access_to_new_pair_and_return_restores_old_history(
    client: httpx.AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    make_messaging_care: MakeMessagingCare,
    make_user: MakeUser,
) -> None:
    care = await make_messaging_care("first-lead@example.com", "moving-patient@example.com")
    replacement = await make_user(email="second-lead@example.com")
    patient_id = care.patient.id
    await login(client, care.patient.email)
    old = await _send(client, patient_id, care.doctor.id, "Сообщение первому врачу.")
    assert old.status_code == 201

    await _reassign(sessionmaker, patient_id, replacement.id)

    await _assert_former_doctor_is_locked_out(client, care.doctor, patient_id)

    await login(client, care.patient.email)
    stale_read = await client.post(
        f"{_message_url(patient_id)}/read",
        json={"expected_doctor_id": str(care.doctor.id), "through_seq": 0},
    )
    assert stale_read.status_code == 409
    assert stale_read.json()["code"] == "conversation_changed"
    new_pair = await _history(client, patient_id)
    assert new_pair["doctor_id"] == str(replacement.id)
    assert new_pair["items"] == []
    assert new_pair["last_seq"] == 0
    new = await _send(client, patient_id, replacement.id, "Сообщение новому врачу.")
    assert new.status_code == 201
    assert new.json()["seq"] == 1
    assert _ids(await _history(client, patient_id)) == [new.json()["id"]]

    await login(client, replacement.email)
    assert _ids(await _history(client, patient_id)) == [new.json()["id"]]

    await _reassign(sessionmaker, patient_id, care.doctor.id)

    await _assert_former_doctor_is_locked_out(client, replacement, patient_id)
    await login(client, care.doctor.email)
    restored = await _history(client, patient_id)
    assert restored["doctor_id"] == str(care.doctor.id)
    assert _ids(restored) == [old.json()["id"]]
    await login(client, care.patient.email)
    assert _ids(await _history(client, patient_id)) == [old.json()["id"]]
