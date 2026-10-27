"""Тестовый каталог шкал, отделённый от рабочей базы клинических данных."""

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import asyncpg
import httpx
import pytest
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from httpx import ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.live import PASSWORD

from clin_compass.core.config import Settings
from clin_compass.core.queue import asyncpg_dsn
from clin_compass.core.realtime import CHANNEL
from clin_compass.main import create_app
from clin_compass.modules.identity import service as identity
from clin_compass.modules.identity.schemas import Role, UserOut
from clin_compass.modules.patients import service as patients

KNOWLEDGE = Path(__file__).parent / "knowledge"
BASE = "/api/v1/assessments"


@dataclass(frozen=True)
class AssessmentCare:
    """Ведущий врач и пациент, созданный через обычный сценарий регистрации."""

    doctor: UserOut
    patient: UserOut


MakeAssessmentCare = Callable[[str, str], Awaitable[AssessmentCare]]


@dataclass
class AssessmentNotifications:
    """Уведомления PostgreSQL, подтверждённые коммитом транзакции."""

    events: list[dict[str, Any]]

    def on_notify(self, _connection: object, _pid: int, _channel: str, payload: str) -> None:
        self.events.append(json.loads(payload))

    async def wait_for(self, count: int) -> list[dict[str, Any]]:
        async with asyncio.timeout(5):
            while len(self.events) < count:
                await asyncio.sleep(0.01)
        return self.events

    async def settle(self) -> list[dict[str, Any]]:
        await asyncio.sleep(0.2)
        return self.events


async def login(client: httpx.AsyncClient, email: str) -> None:
    """Войти синтетическим пользователем с общим тестовым паролем."""
    response = await client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200


def submission(
    patient_id: str,
    *,
    submission_id: str | None = None,
    scale_id: str = "test-eligible",
    scale_version: int = 1,
    answers: dict[str, int] | None = None,
    completed_at: str = "2026-10-17T14:00:00+03:00",
) -> dict[str, object]:
    """Тело POST /assessments с новым ключом идемпотентности по умолчанию."""
    return {
        "patient_id": patient_id,
        "submission_id": submission_id or str(uuid4()),
        "scale_id": scale_id,
        "scale_version": scale_version,
        "completed_at": completed_at,
        "answers": answers or {"q1": 0, "q2": 0},
    }


async def create_assessment_care(
    sessionmaker: async_sessionmaker[AsyncSession], doctor_email: str, patient_email: str
) -> AssessmentCare:
    """Создать синтетического пациента, связанного с ведущим врачом."""
    async with sessionmaker() as session:
        doctor = await identity.create_user(
            session,
            email=doctor_email,
            password=PASSWORD,
            role=Role.DOCTOR,
            full_name="Ведущий врач",
        )
        invite = await patients.create_invite(session, doctor.id, timedelta(days=1))
        patient = await patients.register_patient(
            session,
            invite_token=invite.token,
            email=patient_email,
            password=PASSWORD,
            full_name="Синтетический пациент",
            consent_version=patients.get_consent_document().version,
        )
        await session.commit()
    return AssessmentCare(doctor=doctor, patient=patient)


@pytest.fixture
async def assessment_app(postgres_url: str) -> AsyncIterator[FastAPI]:
    """API с отдельным синтетическим каталогом шкал."""
    settings = Settings(
        database_url=postgres_url,
        environment="test",
        knowledge_dir=KNOWLEDGE,
        s3_endpoint_url=None,
        s3_access_key=None,
        s3_secret_key=None,
        s3_bucket=None,
        s3_region=None,
        # Тесты расписания сравнивают даты по Москве: пояс из `.env` не подхватываем.
        timezone=ZoneInfo("Europe/Moscow"),
    )
    app = create_app(settings)
    async with LifespanManager(app):
        yield app


@pytest.fixture
async def assessment_client(assessment_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """HTTP-клиент к запущенному API со шкалами для интеграционных проверок."""
    async with httpx.AsyncClient(
        transport=ASGITransport(app=assessment_app), base_url="http://test"
    ) as client:
        yield client


@pytest.fixture
def make_assessment_care(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> MakeAssessmentCare:
    """Фабрика синтетических пар врач-пациент в настоящей тестовой БД."""

    async def _make(doctor_email: str, patient_email: str) -> AssessmentCare:
        return await create_assessment_care(sessionmaker, doctor_email, patient_email)

    return _make


@pytest.fixture
async def assessment_care(make_assessment_care: MakeAssessmentCare) -> AssessmentCare:
    """Пациент и его ведущий врач для одного теста."""
    return await make_assessment_care(
        "assessment-doctor@example.com", "assessment-patient@example.com"
    )


@pytest.fixture
async def assessment_notifications(postgres_url: str) -> AsyncIterator[AssessmentNotifications]:
    """Получить только события, которые Postgres выдал после коммита."""
    collected = AssessmentNotifications(events=[])
    connection = await asyncpg.connect(asyncpg_dsn(postgres_url))
    await connection.add_listener(CHANNEL, collected.on_notify)
    yield collected
    await connection.close()


@pytest.fixture
async def empty_assessment_client(
    postgres_url: str, tmp_path: Path
) -> AsyncIterator[httpx.AsyncClient]:
    """Клиент API с корректным пустым каталогом knowledge: манифест без источников."""
    knowledge_dir = tmp_path / "empty-knowledge"
    (knowledge_dir / "sources").mkdir(parents=True)
    (knowledge_dir / "sources" / "manifest.yaml").write_text("sources: []\n", encoding="utf-8")
    settings = Settings(
        database_url=postgres_url,
        environment="test",
        knowledge_dir=knowledge_dir,
        s3_endpoint_url=None,
        s3_access_key=None,
        s3_secret_key=None,
        s3_bucket=None,
        s3_region=None,
        # Тесты расписания сравнивают даты по Москве: пояс из `.env` не подхватываем.
        timezone=ZoneInfo("Europe/Moscow"),
    )
    app = create_app(settings)
    async with (
        LifespanManager(app) as manager,
        httpx.AsyncClient(
            transport=ASGITransport(app=manager.app), base_url="http://test"
        ) as client,
    ):
        yield client
