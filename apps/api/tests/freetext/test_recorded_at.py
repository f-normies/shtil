"""Время записи у находки из голосового сообщения (спека #96, раздел 3.3).

Голосовое сообщение ставится на разбор из задачи расшифровки, позже самой записи. Находка
должна нести время сообщения.
"""

import pytest
from clin_compass_knowledge.grls import Registry
from clin_compass_knowledge.loader import Knowledge
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.core.config import Settings
from clin_compass.modules.freetext.models import Analysis
from clin_compass.modules.messaging.models import Message
from clin_compass.modules.proposals import service as proposals
from tests.core.fake_nodes import fake_node
from tests.freetext.conftest import Care
from tests.freetext.jobs import make_jobs
from tests.messaging.test_voice_tasks import (
    TRANSCRIPT,
    ControlledS3,
    _configured,
    _handler,
    _patch_s3,
    _voice_message,
)

FINDING = {
    "category": "symptom",
    "summary": "расшифровка",
    "quote": TRANSCRIPT,
    "drug_name": None,
    "adherence_event": None,
}


async def test_voice_finding_records_time_of_message_not_of_analysis(
    settings: Settings,
    sessionmaker: async_sessionmaker[AsyncSession],
    care: Care,
    knowledge: Knowledge,
    registry: Registry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message_id = await _voice_message(sessionmaker, care)
    _patch_s3(monkeypatch, ControlledS3())
    async with fake_node(lambda _: (200, {"text": TRANSCRIPT, "duration": 3.62})) as node:
        await _handler(_configured(settings, node.url), sessionmaker)(str(message_id).encode())
    async with sessionmaker() as session:
        message = await session.get(Message, message_id)
        analysis = await session.scalar(select(Analysis).where(Analysis.source_id == message_id))
    assert message is not None
    assert analysis is not None
    jobs, _ = make_jobs(sessionmaker, knowledge, registry, {"findings": [FINDING], "signs": []})

    await jobs.extract(str(analysis.id).encode())

    async with sessionmaker() as session:
        [made] = await proposals.list_for_patient(session, patient_id=care.patient.id)
    assert analysis.created_at > message.created_at
    assert made.payload["recorded_at"] == message.created_at.isoformat()
