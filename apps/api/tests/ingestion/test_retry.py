"""HTTP: повтор документа `failed` и факты «Уже в карточке» в списке (спека #30, разделы 2 и 4)."""

from datetime import date

import httpx
from fastapi import FastAPI
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.modules.ingestion.models import Document
from tests.freetext.conftest import Care
from tests.ingestion.helpers import MemoryStorage, jobs, login, set_document, upload
from tests.ingestion.samples import pdf
from tests.ingestion.test_api import documents_url


async def test_retry_of_failed_parse_requeues_parsing(
    app: FastAPI,
    client: httpx.AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    care: Care,
) -> None:
    app.state.storage = MemoryStorage()
    await login(client, care.doctor.email)
    created = (await upload(client, care.patient.id, pdf())).json()
    await set_document(
        sessionmaker,
        created["id"],
        status="failed",
        error_code="page_unreadable",
        error_page=1,
        parse_failures=5,
    )

    response = await client.post(f"{documents_url(care)}/{created['id']}/retry")

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["status"], body["error_code"], body["error_page"], body["parse_failures"]) == (
        "parsing",
        None,
        None,
        0,
    )
    assert [j for j, _ in await jobs(sessionmaker)] == ["ingestion.parse", "ingestion.parse"]


async def test_retry_of_failed_extraction_requeues_extraction(
    app: FastAPI,
    client: httpx.AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    care: Care,
) -> None:
    app.state.storage = MemoryStorage()
    await login(client, care.doctor.email)
    created = (await upload(client, care.patient.id, pdf())).json()
    async with sessionmaker() as session:
        found = await session.get(Document, created["id"])
        assert found is not None
        found.text, found.ocr, found.parsed_at = "Текст", False, func.now()
        found.status, found.error_code, found.extract_failures = "failed", "extract_failed", 5
        await session.commit()

    response = await client.post(f"{documents_url(care)}/{created['id']}/retry")

    assert response.status_code == 200
    assert (response.json()["status"], response.json()["extract_failures"]) == ("extracting", 0)
    assert [j for j, _ in await jobs(sessionmaker)][-1] == "ingestion.extract"


async def test_retry_of_document_in_work_is_409(
    app: FastAPI, client: httpx.AsyncClient, care: Care
) -> None:
    app.state.storage = MemoryStorage()
    await login(client, care.doctor.email)
    created = (await upload(client, care.patient.id, pdf())).json()

    response = await client.post(f"{documents_url(care)}/{created['id']}/retry")

    assert (response.status_code, response.json()["code"]) == (409, "document_not_failed")


async def test_list_shows_facts_already_in_card(
    app: FastAPI,
    client: httpx.AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    care: Care,
) -> None:
    app.state.storage = MemoryStorage()
    await login(client, care.doctor.email)
    created = (await upload(client, care.patient.id, pdf())).json()
    known = {
        "kind": "ingestion.diagnosis",
        "payload": {
            "document_id": created["id"],
            "quote": "F33.1",
            "page": 1,
            "ocr": False,
            "icd10": "F33.1",
            "text": "РДР",
            "code_in_text": True,
        },
    }
    await set_document(
        sessionmaker, created["id"], status="done", known=[known], document_date=date(2026, 10, 14)
    )

    [listed] = (await client.get(documents_url(care))).json()

    assert (listed["status"], listed["known"], listed["document_date"]) == (
        "done",
        [known],
        "2026-10-14",
    )
