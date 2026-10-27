"""Помощники тестов задач freetext: задачи на `FunctionModel`, текст на разборе, сигналы."""

from typing import Any
from uuid import UUID, uuid4

from clin_compass_knowledge.grls import Registry
from clin_compass_knowledge.loader import Knowledge
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.core.ai.llm import Llm
from clin_compass.modules.alerts import service as alerts
from clin_compass.modules.alerts.schemas import AlertOut
from clin_compass.modules.freetext import service as freetext
from clin_compass.modules.freetext.analysis import Analyzer
from clin_compass.modules.freetext.models import Analysis
from clin_compass.modules.freetext.tasks import Jobs
from tests.core.fake_nodes import MemoryRecorder
from tests.freetext.conftest import Care
from tests.freetext.helpers import Script

TEXT = "Принимаю авиандор утром. Сплю плохо. Иногда думаю, что не хочу жить."
THOUGHTS = {"kind": "test-thoughts", "quote": "не хочу жить"}
INTENT = {"kind": "test-intent", "quote": "думаю"}
NO_RISK: dict[str, Any] = {"signs": []}
NO_FINDINGS: dict[str, Any] = {"findings": [], "signs": []}


def make_jobs(
    maker: async_sessionmaker[AsyncSession],
    knowledge: Knowledge,
    registry: Registry,
    *answers: dict[str, Any] | Exception,
) -> tuple[Jobs, Script]:
    """Задачи, у которых модель отвечает `answers` по очереди."""
    script = Script(*answers)
    analyzer = Analyzer(
        Llm(script.model, MemoryRecorder()), knowledge.signs.values(), registry, knowledge.manifest
    )
    return Jobs(maker, analyzer), script


async def submit(maker: async_sessionmaker[AsyncSession], care: Care, body: str = TEXT) -> bytes:
    """Текст дневника на разборе; возвращает аргумент задачи — id разбора."""
    async with maker() as session:
        analysis_id = await freetext.submit_text(
            session,
            patient_id=care.patient.id,
            source_type="diary",
            source_id=uuid4(),
            text=body,
            transcript=False,
        )
        await session.commit()
    return str(analysis_id).encode()


async def load_analysis(maker: async_sessionmaker[AsyncSession], payload: bytes) -> Analysis:
    async with maker() as session:
        analysis = await session.get(Analysis, UUID(payload.decode()))
    assert analysis is not None
    return analysis


async def patient_alerts(maker: async_sessionmaker[AsyncSession], care: Care) -> list[AlertOut]:
    async with maker() as session:
        return await alerts.list_for_patient(session, patient_id=care.patient.id)


async def ai_alerts(maker: async_sessionmaker[AsyncSession], care: Care) -> list[AlertOut]:
    return [a for a in await patient_alerts(maker, care) if a.origin_type == "ai_run"]
