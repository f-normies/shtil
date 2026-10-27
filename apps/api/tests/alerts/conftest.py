"""Фикстуры alerts: книга правил из tests/alerts/knowledge, врач с пациентом, перехват NOTIFY."""

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import asyncpg
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.core.config import Settings
from clin_compass.core.queue import asyncpg_dsn
from clin_compass.core.realtime import CHANNEL
from clin_compass.modules.alerts import service as alerts
from clin_compass.modules.alerts.rules import RuleBook
from clin_compass.modules.alerts.schemas import AlertOut
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
    """Приложение в тестах alerts читает выдуманные правила из tests/alerts/knowledge."""
    return settings.model_copy(update={"knowledge_dir": KNOWLEDGE})


@pytest.fixture(scope="session")
def rules() -> RuleBook:
    return RuleBook.load(KNOWLEDGE)


async def make_care(
    maker: async_sessionmaker[AsyncSession], doctor_email: str, patient_email: str
) -> Care:
    async with maker() as session:
        doctor = await identity.create_user(
            session, email=doctor_email, password=PASSWORD, role=Role.DOCTOR, full_name="Врач"
        )
        await session.commit()
    return Care(doctor=doctor, patient=await add_patient(maker, doctor.id, patient_email))


async def add_patient(
    maker: async_sessionmaker[AsyncSession], doctor_id: UUID, patient_email: str
) -> UserOut:
    """Пациент, зарегистрированный по приглашению врача `doctor_id`."""
    async with maker() as session:
        invite = await patients.create_invite(session, doctor_id, timedelta(days=1))
        patient = await patients.register_patient(
            session,
            invite_token=invite.token,
            email=patient_email,
            password=PASSWORD,
            full_name="Пациент",
            consent_version=patients.get_consent_document().version,
        )
        await session.commit()
    return patient


@pytest.fixture
async def care(sessionmaker: async_sessionmaker[AsyncSession]) -> Care:
    return await make_care(sessionmaker, "doctor@example.com", "patient@example.com")


class Notifications:
    """События realtime, ушедшие в канал после коммита."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def on_notify(self, _connection: object, _pid: int, _channel: str, payload: str) -> None:
        self.events.append(json.loads(payload))

    async def wait_for(self, count: int) -> list[dict[str, Any]]:
        """Дождаться `count` событий (не дольше 5 с) и вернуть все пришедшие."""
        async with asyncio.timeout(5):
            while len(self.events) < count:
                await asyncio.sleep(0.01)
        return self.events

    async def settle(self) -> list[dict[str, Any]]:
        """Подождать и вернуть события: для проверки, что лишнего события не было.

        Ждём фиксированное время: проверка «события не было» иначе невозможна.
        """
        await asyncio.sleep(0.2)
        return self.events


@pytest.fixture
async def notifications(postgres_url: str) -> AsyncIterator[Notifications]:
    collected = Notifications()
    connection = await asyncpg.connect(asyncpg_dsn(postgres_url))
    await connection.add_listener(CHANNEL, collected.on_notify)
    yield collected
    await connection.close()


# Ответ «несколько дней» на вопрос о самоповреждении: срабатывает test-self-harm.
SELF_HARM = {"test-mood": {"q1": 0, "q2": 0, "q3": 2, "total": 2}}
# Высокий балл без самоповреждения: срабатывает test-high-mood («желательно»).
HIGH_MOOD = {"test-mood": {"q1": 2, "q2": 3, "q3": 0, "total": 5}}


async def check(
    maker: async_sessionmaker[AsyncSession],
    rules: RuleBook,
    patient_id: UUID,
    results: dict[str, dict[str, float]],
    origin_ref: str = "submission:1",
    *,
    submitted: str = "test-mood",
    commit: bool = True,
) -> list[AlertOut]:
    async with maker() as session:
        fired = await alerts.check_results(
            session,
            rules,
            patient_id=patient_id,
            submitted_scale=submitted,
            results=results,
            origin_ref=origin_ref,
        )
        if commit:
            await session.commit()
        else:
            await session.rollback()
        return fired


async def patient_alerts(
    maker: async_sessionmaker[AsyncSession], patient_id: UUID
) -> list[AlertOut]:
    async with maker() as session:
        return await alerts.list_for_patient(session, patient_id=patient_id)


async def acknowledge(
    maker: async_sessionmaker[AsyncSession], care: Care, alert_id: UUID
) -> AlertOut:
    async with maker() as session:
        alert = await alerts.acknowledge(session, doctor_id=care.doctor.id, alert_id=alert_id)
        await session.commit()
        return alert


async def resolve(maker: async_sessionmaker[AsyncSession], care: Care, alert_id: UUID) -> AlertOut:
    """Решить сигнал о риске вариантом `risk-not-high` из tests/alerts/knowledge (спека #95)."""
    async with maker() as session:
        alert = await alerts.resolve(
            session,
            RuleBook.load(KNOWLEDGE).decisions,
            doctor_id=care.doctor.id,
            alert_id=alert_id,
            decision_id="risk-not-high",
            reason="Связались, риск не высокий",
        )
        await session.commit()
        return alert


def event(type_: str, alert: AlertOut, doctor_id: UUID) -> dict[str, Any]:
    return {"to": [str(doctor_id)], "type": type_, "data": {"alert_id": str(alert.id)}}
