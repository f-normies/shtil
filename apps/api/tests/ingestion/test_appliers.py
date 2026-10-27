"""Appliers: подтверждённый факт — в своё место карточки, отклонённый — никуда (спека #30, 3.5)."""

from datetime import date, timedelta
from typing import Any
from uuid import UUID

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.modules.ingestion import service as ingestion
from clin_compass.modules.patients import service as patients
from clin_compass.modules.patients.schemas import DiagnosisIn
from clin_compass.modules.proposals import service as proposals
from clin_compass.modules.proposals.schemas import ProposalOut
from clin_compass.modules.proposals.service import ApplierRegistry, ProposalApplyError
from clin_compass.modules.therapy import service as therapy
from clin_compass.modules.therapy.deps import Catalog
from tests.freetext.conftest import Care
from tests.ingestion.helpers import MemoryStorage, login
from tests.ingestion.jobs import add_document
from tests.therapy.conftest import Care as TherapyCare
from tests.therapy.test_api import GAD, add_diagnosis, options
from tests.therapy.test_safety_api import set_facts

DRUG = {"name": "моклобемид", "check": "registered", "matches": []}


def payload(document_id: UUID, **fields: Any) -> dict[str, Any]:
    return {"document_id": str(document_id), "quote": "цитата", "page": 1, "ocr": False, **fields}


def diagnosis(document_id: UUID, icd10: str = "F33.1") -> dict[str, Any]:
    return payload(
        document_id, icd10=icd10, text="Рекуррентное депрессивное расстройство", code_in_text=True
    )


def medication(document_id: UUID, name: str, **fields: Any) -> dict[str, Any]:
    values = {
        "name": name,
        "name_in_text": name,
        "renamed": False,
        "dose": "300 мг/сут",
        "regimen": None,
        "status": "current",
        "stopped_on": None,
        "document_date": "2026-10-14",
        "drug": DRUG,
        **fields,
    }
    return payload(document_id, **values)


def fact(document_id: UUID, field: str, text: str) -> dict[str, Any]:
    return payload(document_id, field=field, fact=text)


async def offer(
    maker: async_sessionmaker[AsyncSession], care: Care, kind: str, body: dict[str, Any]
) -> ProposalOut:
    async with maker() as session:
        created = await proposals.create_proposal(
            session,
            patient_id=care.patient.id,
            kind=kind,
            payload=body,
            origin_type="ai_run",
            origin_ref="00000000-0000-0000-0000-000000000000",
        )
        await session.commit()
    return created


async def document_id(maker: async_sessionmaker[AsyncSession], care: Care) -> UUID:
    return UUID((await add_document(maker, care, MemoryStorage(), status="done")).decode())


async def decide(
    maker: async_sessionmaker[AsyncSession],
    catalog: Catalog,
    care: Care,
    proposal: ProposalOut,
    *,
    confirm: bool = True,
) -> None:
    registry = ApplierRegistry()
    ingestion.register_appliers(registry, catalog)
    async with maker() as session:
        await proposals.decide(
            session,
            registry,
            doctor_id=care.doctor.id,
            proposal_id=proposal.id,
            confirm=confirm,
            comment=None,
        )
        await session.commit()


async def test_diagnosis_goes_to_card_and_rejects_open_therapy_proposals(
    sessionmaker: async_sessionmaker[AsyncSession], care: Care, catalog: Catalog
) -> None:
    doc = await document_id(sessionmaker, care)
    therapy_offer = await offer(sessionmaker, care, "therapy.prescription", {"drug_id": "x"})
    found = await offer(sessionmaker, care, "ingestion.diagnosis", diagnosis(doc))

    await decide(sessionmaker, catalog, care, found)

    async with sessionmaker() as session:
        [saved] = await patients.list_diagnoses(session, care.patient.id)
        stale = await proposals.get_proposal(session, therapy_offer.id)
    assert (saved.icd10, saved.title, saved.set_by) == (
        "F33.1",
        "Рекуррентное депрессивное расстройство",
        care.doctor.id,
    )
    assert stale is not None and (stale.status, stale.decision_comment) == (
        "rejected",
        "профиль изменился",
    )


async def test_repeated_diagnosis_code_is_422(
    client: httpx.AsyncClient, sessionmaker: async_sessionmaker[AsyncSession], care: Care
) -> None:
    doc = await document_id(sessionmaker, care)
    async with sessionmaker() as session:
        await patients.add_diagnosis(
            session,
            care.patient.id,
            DiagnosisIn(icd10="F33.1", title="Диагноз"),
            doctor_id=care.doctor.id,
        )
        await session.commit()
    found = await offer(sessionmaker, care, "ingestion.diagnosis", diagnosis(doc))
    await login(client, care.doctor.email)

    response = await client.post(f"/api/v1/proposals/{found.id}/confirm", json={})

    assert response.status_code == 422
    assert response.json()["detail"] == "Диагноз с этим кодом уже есть в карточке"
    async with sessionmaker() as session:
        again = await proposals.get_proposal(session, found.id)
    assert again is not None and again.status == "proposed"


async def test_current_medication_goes_to_therapy_with_registry_check(
    sessionmaker: async_sessionmaker[AsyncSession], care: Care, catalog: Catalog
) -> None:
    doc = await document_id(sessionmaker, care)
    found = await offer(sessionmaker, care, "ingestion.medication", medication(doc, "Суматриптан"))

    await decide(sessionmaker, catalog, care, found)

    async with sessionmaker() as session:
        [saved] = await therapy.list_medications(session, care.patient.id)
    assert (saved.name, saved.inn, saved.registry_check, saved.stopped_on) == (
        "Суматриптан",
        "Суматриптан",
        "registered",
        None,
    )


async def test_current_medication_already_in_card_is_refused(
    sessionmaker: async_sessionmaker[AsyncSession], care: Care, catalog: Catalog
) -> None:
    doc = await document_id(sessionmaker, care)
    first = await offer(sessionmaker, care, "ingestion.medication", medication(doc, "Суматриптан"))
    second = await offer(sessionmaker, care, "ingestion.medication", medication(doc, "суматриптан"))
    await decide(sessionmaker, catalog, care, first)

    with pytest.raises(ProposalApplyError, match="Препарат уже внесён"):
        await decide(sessionmaker, catalog, care, second)


async def test_stopped_maoi_from_history_blocks_drug_in_window(
    client: httpx.AsyncClient, sessionmaker: async_sessionmaker[AsyncSession], care: Care
) -> None:
    # Сценарий 3: моклобемид отменён 10 дней назад, `test-drug` противопоказан ещё 4 дня.
    doc = await document_id(sessionmaker, care)
    stopped = (date.today() - timedelta(days=10)).isoformat()
    found = await offer(
        sessionmaker,
        care,
        "ingestion.medication",
        medication(doc, "моклобемид", status="past", stopped_on=stopped),
    )
    await login(client, care.doctor.email)
    card = TherapyCare(client, care.patient.id)
    await set_facts(card)
    await add_diagnosis(card, GAD)

    confirmed = await client.post(f"/api/v1/proposals/{found.id}/confirm", json={})
    [option] = (await options(card))["options"]

    assert confirmed.status_code == 200, confirmed.text
    async with sessionmaker() as session:
        [saved] = await therapy.list_medications(session, care.patient.id)
    assert (saved.inn, saved.stopped_on) == ("Моклобемид", date.fromisoformat(stopped))
    maoi = next(c for c in option["checks"] if c["id"] == "contra:maoi")
    assert (maoi["status"], option["proposal"]) == ("fail", None)


@pytest.mark.parametrize(
    ("kind", "build", "field"),
    [
        (
            "ingestion.medication",
            lambda d: medication(d, "пароксетин", status="past"),
            "past_medication",
        ),
        ("ingestion.fact", lambda d: fact(d, "anamnesis", "первый эпизод в 2019 г."), "anamnesis"),
        (
            "ingestion.fact",
            lambda d: fact(d, "risk_factor", "отец злоупотреблял алкоголем"),
            "risk_factor",
        ),
    ],
)
async def test_other_facts_go_to_ingestion_facts(
    sessionmaker: async_sessionmaker[AsyncSession],
    care: Care,
    catalog: Catalog,
    kind: str,
    build: Any,
    field: str,
) -> None:
    doc = await document_id(sessionmaker, care)
    body = build(doc)
    found = await offer(sessionmaker, care, kind, body)

    await decide(sessionmaker, catalog, care, found)

    async with sessionmaker() as session:
        [saved] = await ingestion.list_confirmed(session, patient_id=care.patient.id)
        medications = await therapy.list_medications(session, care.patient.id)
    assert (saved.field, saved.proposal_id, saved.document_id, saved.data) == (
        field,
        found.id,
        doc,
        body,
    )
    assert saved.confirmed_by == care.doctor.id
    assert medications == []


async def test_rejection_writes_nothing(
    sessionmaker: async_sessionmaker[AsyncSession], care: Care, catalog: Catalog
) -> None:
    doc = await document_id(sessionmaker, care)
    for kind, body in (
        ("ingestion.diagnosis", diagnosis(doc)),
        ("ingestion.medication", medication(doc, "Суматриптан")),
        ("ingestion.fact", fact(doc, "anamnesis", "первый эпизод")),
    ):
        await decide(
            sessionmaker, catalog, care, await offer(sessionmaker, care, kind, body), confirm=False
        )

    async with sessionmaker() as session:
        assert await patients.list_diagnoses(session, care.patient.id) == []
        assert await therapy.list_medications(session, care.patient.id) == []
        assert await ingestion.list_confirmed(session, patient_id=care.patient.id) == []


async def test_list_confirmed_filters_by_field(
    sessionmaker: async_sessionmaker[AsyncSession], care: Care, catalog: Catalog
) -> None:
    doc = await document_id(sessionmaker, care)
    for body in (fact(doc, "anamnesis", "первый эпизод"), fact(doc, "risk_factor", "живёт один")):
        await decide(
            sessionmaker, catalog, care, await offer(sessionmaker, care, "ingestion.fact", body)
        )

    async with sessionmaker() as session:
        risks = await ingestion.list_confirmed(
            session, patient_id=care.patient.id, fields=["risk_factor"]
        )
        everything = await ingestion.list_confirmed(session, patient_id=care.patient.id)

    assert [f.data["fact"] for f in risks] == ["живёт один"]
    assert [f.field for f in everything] == ["anamnesis", "risk_factor"]
