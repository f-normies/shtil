"""Документы и подтверждённые факты физически не удаляются: это запрещают триггеры БД."""

from uuid import uuid4

import pytest
from sqlalchemy import delete, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.modules.ingestion.models import Document, Fact
from tests.freetext.conftest import Care


async def _document(maker: async_sessionmaker[AsyncSession], care: Care) -> Document:
    async with maker() as session:
        document = Document(
            patient_id=care.patient.id,
            uploaded_by=care.doctor.id,
            filename="history.pdf",
            content_type="application/pdf",
            size=10,
            sha256="0" * 64,
            object_key=f"documents/{uuid4()}",
            pages=1,
        )
        session.add(document)
        await session.commit()
        return document


async def test_document_cannot_be_deleted(
    sessionmaker: async_sessionmaker[AsyncSession], care: Care
) -> None:
    await _document(sessionmaker, care)

    async with sessionmaker() as session:
        with pytest.raises(DBAPIError, match="keeps medical data"):
            await session.execute(delete(Document))


async def test_confirmed_fact_cannot_be_changed_or_deleted(
    sessionmaker: async_sessionmaker[AsyncSession], care: Care
) -> None:
    document = await _document(sessionmaker, care)
    async with sessionmaker() as session:
        session.add(
            Fact(
                proposal_id=uuid4(),
                document_id=document.id,
                patient_id=care.patient.id,
                field="anamnesis",
                data={"fact": "первый эпизод в 2019 г."},
                confirmed_by=care.doctor.id,
            )
        )
        await session.commit()

    async with sessionmaker() as session:
        with pytest.raises(DBAPIError, match="keeps medical data"):
            await session.execute(update(Fact).values(field="risk_factor"))
    async with sessionmaker() as session:
        with pytest.raises(DBAPIError, match="keeps medical data"):
            await session.execute(delete(Fact))
