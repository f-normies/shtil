"""API с тестовым справочником препаратов и пациент с ведущим врачом (спека #28)."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from asgi_lifespan import LifespanManager

from clin_compass.core.config import Settings
from clin_compass.main import create_app
from tests.conftest import MakeUser
from tests.patients.test_access import login, register_patient

KNOWLEDGE = Path(__file__).parent / "knowledge"
DOCTOR = "doctor@example.com"
PATIENT = "patient@example.com"


@dataclass(frozen=True)
class Care:
    client: httpx.AsyncClient
    patient_id: UUID

    @property
    def base(self) -> str:
        return f"/api/v1/patients/{self.patient_id}"


@pytest.fixture
async def therapy_client(postgres_url: str) -> AsyncIterator[httpx.AsyncClient]:
    """Клиент API, у которого knowledge/ — тестовый справочник `tests/therapy/knowledge`."""
    settings = Settings(
        database_url=postgres_url,
        environment="test",
        knowledge_dir=KNOWLEDGE,
        llm_urls=(),
        embeddings_urls=(),
        asr_urls=(),
        s3_endpoint_url=None,
        s3_access_key=None,
        s3_secret_key=None,
        s3_bucket=None,
        s3_region=None,
    )
    async with (
        LifespanManager(create_app(settings)) as manager,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=manager.app), base_url="http://test"
        ) as client,
    ):
        yield client


@pytest.fixture
async def care(therapy_client: httpx.AsyncClient, make_user: MakeUser) -> Care:
    """Врач вошёл; у него пациент без профиля и диагнозов."""
    await make_user(email=DOCTOR)
    patient_id = await register_patient(therapy_client, DOCTOR, PATIENT)
    await login(therapy_client, DOCTOR)
    return Care(therapy_client, patient_id)
