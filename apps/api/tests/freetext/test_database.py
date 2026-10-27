"""Медицинские данные freetext физически не удаляются: это запрещают триггеры БД."""

from uuid import uuid4

import pytest
from sqlalchemy import delete, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.modules.freetext.models import Analysis, Finding
from tests.freetext.conftest import Care


async def _analysis(maker: async_sessionmaker[AsyncSession], care: Care) -> Analysis:
    async with maker() as session:
        analysis = Analysis(
            patient_id=care.patient.id,
            source_type="diary",
            source_id=uuid4(),
            transcript=False,
            text="Плохо сплю.",
        )
        session.add(analysis)
        await session.commit()
        return analysis


async def test_analysis_cannot_be_deleted(
    sessionmaker: async_sessionmaker[AsyncSession], care: Care
) -> None:
    await _analysis(sessionmaker, care)

    async with sessionmaker() as session:
        with pytest.raises(DBAPIError, match="keeps medical data"):
            await session.execute(delete(Analysis))


async def test_confirmed_finding_cannot_be_changed_or_deleted(
    sessionmaker: async_sessionmaker[AsyncSession], care: Care
) -> None:
    analysis = await _analysis(sessionmaker, care)
    async with sessionmaker() as session:
        session.add(
            Finding(
                proposal_id=uuid4(),
                analysis_id=analysis.id,
                patient_id=care.patient.id,
                category="symptom",
                data={"summary": "бессонница"},
                confirmed_by=care.doctor.id,
            )
        )
        await session.commit()

    async with sessionmaker() as session:
        with pytest.raises(DBAPIError, match="keeps medical data"):
            await session.execute(update(Finding).values(category="side_effect"))
    async with sessionmaker() as session:
        with pytest.raises(DBAPIError, match="keeps medical data"):
            await session.execute(delete(Finding))
