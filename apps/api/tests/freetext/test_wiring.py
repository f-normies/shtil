"""Модуль freetext подключён к приложению и к worker'у (спека #32, разделы 3.1 и 4.1)."""

import shutil
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.core.config import Settings
from clin_compass.core.queue import Entrypoint
from clin_compass.modules.freetext import service as freetext
from clin_compass.modules.proposals import service as proposals
from clin_compass.worker import build_handlers
from tests.freetext.conftest import KNOWLEDGE, Care
from tests.live import PASSWORD


async def test_doctor_confirms_finding_through_api(
    client: httpx.AsyncClient, sessionmaker: async_sessionmaker[AsyncSession], care: Care
) -> None:
    async with sessionmaker() as session:
        analysis_id = await freetext.submit_text(
            session,
            patient_id=care.patient.id,
            source_type="message",
            source_id=uuid4(),
            text="Сплю плохо.",
            transcript=False,
        )
        proposal = await proposals.create_proposal(
            session,
            patient_id=care.patient.id,
            kind="freetext.finding",
            payload={"analysis_id": str(analysis_id), "category": "symptom", "quote": "Сплю плохо"},
            origin_type="ai_run",
            origin_ref=str(uuid4()),
        )
        await session.commit()
    login = {"email": care.doctor.email, "password": PASSWORD}
    assert (await client.post("/api/v1/auth/login", json=login)).status_code == 200

    response = await client.post(f"/api/v1/proposals/{proposal.id}/confirm", json={})

    assert response.status_code == 200
    async with sessionmaker() as session:
        [finding] = await freetext.list_confirmed(session, patient_id=care.patient.id)
    assert finding.proposal_id == proposal.id


def test_worker_registers_both_stages_with_one_slot_each(
    settings: Settings, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    configured = settings.model_copy(update={"llm_urls": ("http://127.0.0.1:8000",)})

    handlers = build_handlers(configured, sessionmaker)

    stages = {name: h for name, h in handlers.items() if name.startswith("freetext.")}
    assert sorted(stages) == ["freetext.check_risk", "freetext.extract"]
    assert all(isinstance(h, Entrypoint) and h.concurrency_limit == 1 for h in stages.values())


def test_worker_without_llm_leaves_texts_in_queue(
    settings: Settings, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    # Узлы LLM не заданы: задачи ждут в очереди, пока их не настроят.
    handlers = build_handlers(settings, sessionmaker)
    assert not any(name.startswith("freetext.") for name in handlers)


def test_worker_does_not_start_without_reviewed_signs(
    settings: Settings, sessionmaker: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    # Проверка риска без видов признаков молча ничего бы не находила.
    root = tmp_path / "knowledge"
    shutil.copytree(KNOWLEDGE, root)
    for sign in (root / "signs").iterdir():
        sign.write_text(sign.read_text().replace("status: reviewed", "status: draft"))
    no_signs = settings.model_copy(
        update={"llm_urls": ("http://127.0.0.1:8000",), "knowledge_dir": root}
    )

    with pytest.raises(ValueError, match="нет видов признаков риска"):
        build_handlers(no_signs, sessionmaker)
