"""Синхронизация сводки непрочитанного со сменой врача."""

import asyncio
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.modules.messaging import service as messaging
from clin_compass.modules.patients.models import Patient
from tests.conftest import MakeUser
from tests.messaging.conftest import MakeMessagingCare, MessagingCare, login


def _url(patient_id: str) -> str:
    return f"/api/v1/patients/{patient_id}/messages"


async def test_patient_unread_holds_pair_lock_until_summary_is_read(
    client: httpx.AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    make_messaging_care: MakeMessagingCare,
    make_user: MakeUser,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    care = await make_messaging_care("unread-patient-doctor@example.com", "unread-self@example.com")
    await login(client, care.doctor.email)
    sent = await client.post(
        _url(str(care.patient.id)),
        json={
            "expected_doctor_id": str(care.doctor.id),
            "client_message_id": str(uuid4()),
            "text": "Непрочитанное сообщение пациенту.",
        },
    )
    assert sent.status_code == 201

    new_doctor = await make_user(email="unread-patient-new-doctor@example.com")
    await login(client, care.patient.email)
    lock_acquired = asyncio.Event()
    continue_read = asyncio.Event()
    original_lock = messaging.patients.lead_doctor_of_for_update

    async def lock_then_pause(session: AsyncSession, patient_id: UUID) -> UUID | None:
        doctor_id = await original_lock(session, patient_id)
        lock_acquired.set()
        await continue_read.wait()
        return doctor_id

    monkeypatch.setattr(messaging.patients, "lead_doctor_of_for_update", lock_then_pause)
    read_task = asyncio.create_task(client.get("/api/v1/messaging/unread"))
    assignment_started = asyncio.Event()
    assignment_done = asyncio.Event()

    async def reassign_patient() -> None:
        async with sessionmaker() as session:
            patient = await session.get(Patient, care.patient.id)
            assert patient is not None
            patient.lead_doctor_id = new_doctor.id
            assignment_started.set()
            await session.commit()
        assignment_done.set()

    assignment_task: asyncio.Task[None] | None = None
    try:
        await asyncio.wait_for(lock_acquired.wait(), timeout=3)
        assignment_task = asyncio.create_task(reassign_patient())
        await asyncio.wait_for(assignment_started.wait(), timeout=3)
        done, _ = await asyncio.wait({assignment_task}, timeout=0.1)
        assert assignment_task not in done
    finally:
        continue_read.set()
        if assignment_task is None:
            await asyncio.gather(read_task, return_exceptions=True)
        else:
            await asyncio.gather(read_task, assignment_task, return_exceptions=True)

    response = await read_task
    await asyncio.wait_for(assignment_done.wait(), timeout=3)
    assert response.status_code == 200
    assert response.json()[0]["last_message_id"] == sent.json()["id"]

    after_assignment = await client.get("/api/v1/messaging/unread")
    assert after_assignment.status_code == 200
    assert after_assignment.json() == []


async def test_doctor_unread_locks_multiple_patients_in_uuid_order(
    client: httpx.AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    make_messaging_care: MakeMessagingCare,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cares: list[MessagingCare] = []
    for index in range(3):
        cares.append(
            await make_messaging_care(
                f"unread-order-doctor-{index}@example.com",
                f"unread-order-patient-{index}@example.com",
            )
        )
    doctor = cares[0].doctor
    async with sessionmaker() as session:
        for care in cares:
            patient = await session.get(Patient, care.patient.id)
            assert patient is not None
            patient.lead_doctor_id = doctor.id
        await session.commit()

    for index, care in enumerate(cares):
        await login(client, care.patient.email)
        sent = await client.post(
            _url(str(care.patient.id)),
            json={
                "expected_doctor_id": str(doctor.id),
                "client_message_id": str(uuid4()),
                "text": f"Сообщение пациента {index}.",
            },
        )
        assert sent.status_code == 201

    locked_patient_ids: list[UUID] = []
    original_lock = messaging.patients.lead_doctor_of_for_update

    async def record_lock(session: AsyncSession, patient_id: UUID) -> UUID | None:
        locked_patient_ids.append(patient_id)
        return await original_lock(session, patient_id)

    monkeypatch.setattr(messaging.patients, "lead_doctor_of_for_update", record_lock)
    await login(client, doctor.email)
    response = await client.get("/api/v1/messaging/unread")

    assert response.status_code == 200
    assert len(response.json()) == len(cares)
    assert locked_patient_ids == sorted(care.patient.id for care in cares)
