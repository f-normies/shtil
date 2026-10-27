"""Результат врача проходит правила сигналов: настоящий каталог `knowledge/` (спека #94)."""

from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

import httpx
import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.assessments.conftest import MakeAssessmentCare, login

from clin_compass.core.config import Settings
from clin_compass.main import create_app
from clin_compass.modules.alerts.models import Alert
from clin_compass.modules.alerts.rules import repository_knowledge_dir


@pytest.fixture
async def repo_client(postgres_url: str) -> AsyncIterator[httpx.AsyncClient]:
    """API на настоящих шкалах и правилах репозитория."""
    settings = Settings(
        database_url=postgres_url, environment="test", knowledge_dir=repository_knowledge_dir()
    )
    app = create_app(settings)
    async with (
        LifespanManager(app) as manager,
        httpx.AsyncClient(transport=ASGITransport(app=manager.app), base_url="http://test") as c,
    ):
        yield c


async def definition_of(client: httpx.AsyncClient, scale_id: str) -> Any:
    return (await client.get(f"/api/v1/assessments/scales/{scale_id}")).json()["scale"]


def answers_for(definition: Any, values: dict[str, int]) -> dict[str, int]:
    return {item["id"]: values.get(item["id"], 0) for item in definition["items"]}


HARS_25 = {f"q{i}": 2 for i in range(1, 13)} | {"q13": 1}

# (id, шкала, ответы, ожидаемые правила, источник)
CASES = [
    (
        "hamd_q3_2_no_signal",
        "hamd-17",
        {"q3": 2},
        [],
        "hamd17-suicidal-intent; КР депрессии, прил. Г1, п. 3: 2 — мысли о смерти",
    ),
    (
        "hamd_q3_3_mandatory",
        "hamd-17",
        {"q3": 3},
        ["hamd17-suicidal-intent"],
        "hamd17-suicidal-intent; КР депрессии, прил. Г1, п. 3: 3 — суицидальные высказывания",
    ),
    (
        "hars_25_routing_mandatory",
        "hars",
        HARS_25,
        ["routing-anxiety-severe"],
        "routing-anxiety-severe; КР ГТР, раздел 6: HARS от 25",
    ),
]


@pytest.mark.parametrize(
    ("scale_id", "values", "rules"), [c[1:4] for c in CASES], ids=[c[0] for c in CASES]
)
async def test_clinician_result_raises_rule_signals(
    repo_client: httpx.AsyncClient,
    make_assessment_care: MakeAssessmentCare,
    sessionmaker: async_sessionmaker[AsyncSession],
    scale_id: str,
    values: dict[str, int],
    rules: list[str],
) -> None:
    care = await make_assessment_care("alerts-doctor@example.com", "alerts-patient@example.com")
    await login(repo_client, care.doctor.email)
    definition = await definition_of(repo_client, scale_id)
    response = await repo_client.post(
        f"/api/v1/patients/{care.patient.id}/assessments",
        json={
            "submission_id": str(uuid4()),
            "scale_id": scale_id,
            "scale_version": definition["version"],
            "answers": answers_for(definition, values),
        },
    )

    assert response.status_code == 201
    async with sessionmaker() as session:
        fired = list(
            await session.scalars(select(Alert.rule_id).where(Alert.patient_id == care.patient.id))
        )
    assert sorted(fired) == rules
