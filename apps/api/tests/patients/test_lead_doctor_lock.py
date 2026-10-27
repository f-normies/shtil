"""Чтение ведущего врача под блокировкой видит смену врача из другой транзакции."""

from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.modules.patients import service as patients
from clin_compass.modules.patients.models import Patient
from tests.conftest import MakeUser

PASSWORD = "correct horse battery staple"


async def test_locked_lead_doctor_read_refreshes_patient_already_loaded_in_session(
    sessionmaker: async_sessionmaker[AsyncSession], make_user: MakeUser
) -> None:
    first = await make_user(email="lock-first-doctor@example.com")
    second = await make_user(email="lock-second-doctor@example.com")
    async with sessionmaker() as session:
        invite = await patients.create_invite(session, first.id, timedelta(days=1))
        patient = await patients.register_patient(
            session,
            invite_token=invite.token,
            email="lock-patient@example.com",
            password=PASSWORD,
            full_name="Синтетический пациент",
            consent_version=patients.get_consent_document().version,
        )
        await session.commit()

    async with sessionmaker() as reader:
        loaded = await reader.get(Patient, patient.id)
        assert loaded is not None
        assert loaded.lead_doctor_id == first.id
        async with sessionmaker() as writer:
            reassigned = await writer.get(Patient, patient.id)
            assert reassigned is not None
            reassigned.lead_doctor_id = second.id
            await writer.commit()

        # Объект пациента уже в сессии: без populate_existing вернулся бы прежний врач.
        assert await patients.lead_doctor_of_for_update(reader, patient.id) == second.id
