"""Фикстуры freetext: выдуманные виды признаков и снимок ГРЛС, врач с пациентом."""

from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import pytest
from clin_compass_knowledge.grls import Registry, load_registry
from clin_compass_knowledge.loader import Knowledge, load_knowledge
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.core.config import Settings
from clin_compass.modules.identity import service as identity
from clin_compass.modules.identity.schemas import Role, UserOut
from clin_compass.modules.patients import service as patients
from tests.live import PASSWORD

KNOWLEDGE = Path(__file__).parent / "knowledge"


@dataclass(frozen=True)
class Care:
    doctor: UserOut
    patient: UserOut


@pytest.fixture(scope="session")
def settings(settings: Settings) -> Settings:
    """Приложение в тестах freetext читает выдуманные данные из tests/freetext/knowledge."""
    return settings.model_copy(update={"knowledge_dir": KNOWLEDGE})


@pytest.fixture(scope="session")
def knowledge() -> Knowledge:
    return load_knowledge(KNOWLEDGE)


@pytest.fixture(scope="session")
def registry() -> Registry:
    return load_registry(KNOWLEDGE / "sources" / "files" / "grls.csv")


async def make_care(
    maker: async_sessionmaker[AsyncSession], doctor_email: str, patient_email: str
) -> Care:
    """Врач и пациент, зарегистрированный по его приглашению."""
    async with maker() as session:
        doctor = await identity.create_user(
            session, email=doctor_email, password=PASSWORD, role=Role.DOCTOR, full_name="Врач"
        )
        invite = await patients.create_invite(session, doctor.id, timedelta(days=1))
        patient = await patients.register_patient(
            session,
            invite_token=invite.token,
            email=patient_email,
            password=PASSWORD,
            full_name="Пациент",
            consent_version=patients.get_consent_document().version,
        )
        await session.commit()
    return Care(doctor=doctor, patient=patient)


@pytest.fixture
async def care(sessionmaker: async_sessionmaker[AsyncSession]) -> Care:
    return await make_care(sessionmaker, "doctor@example.com", "patient@example.com")
