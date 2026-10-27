"""Задача `ingestion.extract`: предложения, факты из карточки, повторы (спека #30, 3.4–3.6)."""

from datetime import UTC, date, datetime, timedelta

import pytest
from clin_compass_knowledge.grls import Registry
from pydantic_ai.exceptions import ModelHTTPError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.core.ai.llm import MODEL_ALIAS
from clin_compass.core.ai.nodes import NodesUnavailableError
from clin_compass.core.queue import RetryRequested
from clin_compass.modules.alerts import service as alerts
from clin_compass.modules.patients import service as patients
from clin_compass.modules.patients.schemas import DiagnosisIn
from clin_compass.modules.proposals import service as proposals
from clin_compass.modules.proposals.schemas import ProposalOut
from clin_compass.modules.therapy import service as therapy
from clin_compass.modules.therapy.deps import Catalog
from clin_compass.modules.therapy.schemas import MedicationIn
from tests.freetext.conftest import Care
from tests.ingestion.helpers import MemoryStorage
from tests.ingestion.jobs import add_document, load, make_jobs
from tests.ingestion.test_analysis import answer, diagnosis, fact, medication

PAGE_1 = (
    "Дата: 14.10.2026.\n"
    "Диагноз: Рекуррентное депрессивное расстройство, текущий эпизод средней степени (F33.1)."
)
PAGE_2 = (
    "Принимал Sertralini 50 мг утром, отменён 04.10.2026. Сейчас суматриптан 50 мг при мигрени."
)
TEXT = f"{PAGE_1}\n\n{PAGE_2}"
SPANS = [
    {"page": 1, "source": "layer_markup", "start": 0, "end": len(PAGE_1)},
    {"page": 2, "source": "ocr", "start": len(PAGE_1) + 2, "end": len(TEXT)},
]
SUMATRIPTAN = medication("суматриптан", "суматриптан", "суматриптан 50 мг при мигрени")
SERTRALINE = medication(
    "сертралин",
    "Sertralini",
    "Принимал Sertralini 50 мг утром, отменён 04.10.2026",
    status="past",
    stopped_on="2026-10-04",
)
FULL = answer(
    document_date="2026-10-14",
    diagnoses=[diagnosis()],
    medications=[SUMATRIPTAN, SERTRALINE],
    anamnesis=[fact("Принимал Sertralini 50 мг утром")],
    risk_factors=[fact("Выдуманная цитата")],
)


async def extracting(maker: async_sessionmaker[AsyncSession], care: Care) -> bytes:
    """Распознанный документ, который ждёт извлечения."""
    return await add_document(
        maker,
        care,
        MemoryStorage(),
        status="extracting",
        text=TEXT,
        page_sources=SPANS,
        ocr=True,
        parsed_at=datetime.now(UTC),
    )


async def patient_proposals(
    maker: async_sessionmaker[AsyncSession], care: Care
) -> list[ProposalOut]:
    async with maker() as session:
        return await proposals.list_for_patient(session, patient_id=care.patient.id)


async def test_each_fact_becomes_one_proposal_of_its_kind(
    sessionmaker: async_sessionmaker[AsyncSession], care: Care, registry: Registry
) -> None:
    worker, _ = make_jobs(sessionmaker, registry, answers=(FULL,))
    payload = await extracting(sessionmaker, care)

    await worker.extract(payload)

    document = await load(sessionmaker, payload)
    assert (document.status, document.dropped, document.known) == ("done", 1, [])
    assert document.document_date == date(2026, 10, 14)
    assert document.extract_run_id is not None and document.extracted_at is not None
    offered = await patient_proposals(sessionmaker, care)
    assert [p.kind for p in offered] == [
        "ingestion.diagnosis",
        "ingestion.medication",
        "ingestion.medication",
        "ingestion.fact",
    ]
    assert {(p.origin_type, p.origin_ref, p.status) for p in offered} == {
        ("ai_run", str(document.extract_run_id), "proposed")
    }
    found_diagnosis, current, past, anamnesis = (p.payload for p in offered)
    common = {"document_id": str(document.id), "ocr": True}
    assert found_diagnosis == {
        **common,
        "quote": diagnosis()["quote"],
        "page": 1,
        "icd10": "F33.1",
        "text": diagnosis()["text"],
        "code_in_text": True,
    }
    assert current["page"] == 2
    assert (current["status"], current["stopped_on"], current["document_date"]) == (
        "current",
        None,
        "2026-10-14",
    )
    assert current["drug"]["check"] == "registered"
    assert (past["name"], past["renamed"], past["stopped_on"]) == ("сертралин", True, "2026-10-04")
    assert past["drug"] == {"name": "сертралин", "check": "not_found", "matches": []}
    assert (anamnesis["field"], anamnesis["fact"], anamnesis["page"]) == (
        "anamnesis",
        "Принимал Sertralini 50 мг утром",
        2,
    )


async def test_facts_already_in_card_go_to_known(
    sessionmaker: async_sessionmaker[AsyncSession],
    care: Care,
    registry: Registry,
    catalog: Catalog,
) -> None:
    async with sessionmaker() as session:
        await patients.add_diagnosis(
            session,
            care.patient.id,
            DiagnosisIn(icd10="F33.1", title="РДР"),
            doctor_id=care.doctor.id,
        )
        await therapy.add_medication(
            session,
            catalog,
            care.patient.id,
            MedicationIn(name="Суматриптан"),
            doctor_id=care.doctor.id,
        )
        # Отменённый сертралин в карточке не мешает предложить прошлый приём из документа.
        await therapy.add_medication(
            session,
            catalog,
            care.patient.id,
            MedicationIn(name="сертралин", stopped_on=date(2026, 1, 1)),
            doctor_id=care.doctor.id,
        )
        await session.commit()
    worker, _ = make_jobs(sessionmaker, registry, answers=(FULL,))
    payload = await extracting(sessionmaker, care)

    await worker.extract(payload)

    document = await load(sessionmaker, payload)
    assert [
        (k["kind"], k["payload"].get("icd10") or k["payload"]["name"]) for k in document.known
    ] == [
        ("ingestion.diagnosis", "F33.1"),
        ("ingestion.medication", "суматриптан"),
    ]
    offered = await patient_proposals(sessionmaker, care)
    assert [p.kind for p in offered] == ["ingestion.medication", "ingestion.fact"]
    assert offered[0].payload["name"] == "сертралин"


async def test_resolved_diagnosis_with_same_code_is_proposed_again(
    sessionmaker: async_sessionmaker[AsyncSession], care: Care, registry: Registry
) -> None:
    async with sessionmaker() as session:
        old = await patients.add_diagnosis(
            session,
            care.patient.id,
            DiagnosisIn(icd10="F33.1", title="РДР"),
            doctor_id=care.doctor.id,
        )
        await patients.resolve_diagnosis(session, care.patient.id, old.id, doctor_id=care.doctor.id)
        await session.commit()
    worker, _ = make_jobs(sessionmaker, registry, answers=(answer(diagnoses=[diagnosis()]),))
    payload = await extracting(sessionmaker, care)

    await worker.extract(payload)

    assert [p.kind for p in await patient_proposals(sessionmaker, care)] == ["ingestion.diagnosis"]
    assert (await load(sessionmaker, payload)).known == []


async def test_history_raises_no_alerts(
    sessionmaker: async_sessionmaker[AsyncSession], care: Care, registry: Registry
) -> None:
    # Фактор риска из истории — только предложение врачу, сигнала он не поднимает.
    risk = fact("Принимал Sertralini 50 мг утром")
    worker, _ = make_jobs(sessionmaker, registry, answers=(answer(risk_factors=[risk]),))
    payload = await extracting(sessionmaker, care)

    await worker.extract(payload)

    async with sessionmaker() as session:
        assert await alerts.list_for_patient(session, patient_id=care.patient.id) == []


async def test_unavailable_llm_waits_without_counting(
    sessionmaker: async_sessionmaker[AsyncSession], care: Care, registry: Registry
) -> None:
    worker, _ = make_jobs(sessionmaker, registry, answers=(NodesUnavailableError("llm"),))
    payload = await extracting(sessionmaker, care)

    with pytest.raises(RetryRequested) as retry:
        await worker.extract(payload)

    assert retry.value.delay == timedelta(seconds=30)
    document = await load(sessionmaker, payload)
    assert (document.status, document.extract_failures) == ("extracting", 0)


async def test_five_errors_mark_extract_failed(
    sessionmaker: async_sessionmaker[AsyncSession], care: Care, registry: Registry
) -> None:
    error = ModelHTTPError(status_code=500, model_name=MODEL_ALIAS)
    worker, _ = make_jobs(sessionmaker, registry, answers=(error,))
    payload = await extracting(sessionmaker, care)
    delays = []
    for _ in range(4):
        with pytest.raises(RetryRequested) as retry:
            await worker.extract(payload)
        delays.append(retry.value.delay.total_seconds())

    await worker.extract(payload)

    assert delays == [2, 4, 8, 16]
    document = await load(sessionmaker, payload)
    assert (document.status, document.error_code, document.extract_failures) == (
        "failed",
        "extract_failed",
        5,
    )
    assert document.text == TEXT


async def test_repeated_job_after_result_does_not_call_model_again(
    sessionmaker: async_sessionmaker[AsyncSession], care: Care, registry: Registry
) -> None:
    worker, script = make_jobs(sessionmaker, registry, answers=(FULL,))
    payload = await extracting(sessionmaker, care)

    await worker.extract(payload)
    await worker.extract(payload)

    assert len(script.calls) == 1
    assert len(await patient_proposals(sessionmaker, care)) == 4
