"""Сценарий 3: «моклобемид отменён 10 дней назад» из истории болезни закрывает «Авиандр».

Справочник — `tests/therapy/knowledge`: у `test-drug` противопоказание «ингибиторы МАО
и 2 недели после отмены». Дата отмены вычисляется от даты документа (спека #30, раздел 5).
"""

from datetime import UTC, date, datetime, timedelta

import httpx
from clin_compass_knowledge.grls import Registry
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.modules.proposals import service as proposals
from clin_compass.modules.therapy import service as therapy
from tests.freetext.conftest import Care
from tests.ingestion.helpers import MemoryStorage, login
from tests.ingestion.jobs import add_document, make_jobs
from tests.ingestion.test_analysis import answer, medication
from tests.therapy.conftest import Care as TherapyCare
from tests.therapy.test_api import GAD, add_diagnosis, options
from tests.therapy.test_safety_api import set_facts

QUOTE = "В частной клинике назначен моклобемид 300 мг/сут, отменён 10 дней назад"


async def test_stop_ten_days_before_document_blocks_drug_in_window(
    client: httpx.AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    care: Care,
    registry: Registry,
) -> None:
    today = date.today()
    text = f"Протокол приёма. Дата: {today:%d.%m.%Y}.\n{QUOTE}."
    reply = answer(
        document_date=today.isoformat(),
        medications=[
            medication(
                "моклобемид",
                "моклобемид",
                QUOTE,
                dose="300 мг/сут",
                regimen=None,
                status="past",
                stopped_days_before_document=10,
            )
        ],
    )
    worker, _ = make_jobs(sessionmaker, registry, answers=(reply,))
    payload = await add_document(
        sessionmaker,
        care,
        MemoryStorage(),
        status="extracting",
        text=text,
        page_sources=[{"page": 1, "source": "layer_markup", "start": 0, "end": len(text)}],
        ocr=False,
        parsed_at=datetime.now(UTC),
    )
    await login(client, care.doctor.email)
    card = TherapyCare(client, care.patient.id)
    await set_facts(card)
    await add_diagnosis(card, GAD)

    await worker.extract(payload)
    async with sessionmaker() as session:
        [offer] = await proposals.list_open(
            session, patient_id=care.patient.id, kind="ingestion.medication"
        )
    confirmed = await client.post(f"/api/v1/proposals/{offer.id}/confirm", json={})
    [option] = (await options(card))["options"]

    stopped = today - timedelta(days=10)
    assert (offer.payload["stopped_on"], offer.payload["stopped_on_source"]) == (
        stopped.isoformat(),
        "computed",
    )
    assert confirmed.status_code == 200, confirmed.text
    async with sessionmaker() as session:
        [saved] = await therapy.list_medications(session, care.patient.id)
    assert (saved.inn, saved.stopped_on) == ("Моклобемид", stopped)
    maoi = next(c for c in option["checks"] if c["id"] == "contra:maoi")
    assert (maoi["status"], option["proposal"]) == ("fail", None)
