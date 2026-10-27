"""Интерфейс модуля freetext для соседей: приём текста, статус, подтверждённые находки (3.1)."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.modules.freetext import service as freetext
from clin_compass.modules.freetext.models import Analysis
from clin_compass.modules.proposals import service as proposals
from clin_compass.modules.proposals.service import ApplierRegistry
from tests.freetext.conftest import Care


async def _jobs(maker: async_sessionmaker[AsyncSession]) -> list[tuple[str, int, bytes]]:
    async with maker() as session:
        rows = await session.execute(
            text("SELECT entrypoint, priority, payload FROM pgq.pgqueuer ORDER BY id")
        )
        return [(r.entrypoint, r.priority, r.payload) for r in rows]


async def _submit(
    maker: async_sessionmaker[AsyncSession],
    care: Care,
    source_id: UUID,
    body: str = "Плохо сплю.",
) -> UUID:
    async with maker() as session:
        analysis_id = await freetext.submit_text(
            session,
            patient_id=care.patient.id,
            source_type="diary",
            source_id=source_id,
            text=body,
            transcript=False,
        )
        await session.commit()
    return analysis_id


async def test_submit_creates_analysis_and_two_jobs(
    sessionmaker: async_sessionmaker[AsyncSession], care: Care
) -> None:
    analysis_id = await _submit(sessionmaker, care, uuid4())

    # Проверка риска идёт первой: у неё приоритет выше.
    payload = str(analysis_id).encode()
    assert await _jobs(sessionmaker) == [
        ("freetext.check_risk", 10, payload),
        ("freetext.extract", 0, payload),
    ]
    async with sessionmaker() as session:
        analysis = await session.get(Analysis, analysis_id)
    assert analysis is not None
    assert (analysis.text, analysis.source_type, analysis.transcript) == (
        "Плохо сплю.",
        "diary",
        False,
    )


async def test_rolled_back_submit_leaves_no_analysis_and_no_jobs(
    sessionmaker: async_sessionmaker[AsyncSession], care: Care
) -> None:
    async with sessionmaker() as session:
        await freetext.submit_text(
            session,
            patient_id=care.patient.id,
            source_type="message",
            source_id=uuid4(),
            text="Тревожно.",
            transcript=False,
        )
        await session.rollback()

    assert await _jobs(sessionmaker) == []
    async with sessionmaker() as session:
        assert await session.scalar(text("SELECT count(*) FROM freetext.analyses")) == 0


async def test_repeated_submit_returns_same_analysis_without_new_jobs(
    sessionmaker: async_sessionmaker[AsyncSession], care: Care
) -> None:
    source_id = uuid4()
    first = await _submit(sessionmaker, care, source_id)

    second = await _submit(sessionmaker, care, source_id, body="Другой текст.")

    assert second == first
    assert len(await _jobs(sessionmaker)) == 2


@pytest.mark.parametrize("body", ["", "   \n", "а" * 20_001])
async def test_empty_or_too_long_text_is_rejected(
    sessionmaker: async_sessionmaker[AsyncSession], care: Care, body: str
) -> None:
    async with sessionmaker() as session:
        with pytest.raises(ValueError):
            await freetext.submit_text(
                session,
                patient_id=care.patient.id,
                source_type="diary",
                source_id=uuid4(),
                text=body,
                transcript=False,
            )


async def test_longest_allowed_text_is_accepted(
    sessionmaker: async_sessionmaker[AsyncSession], care: Care
) -> None:
    await _submit(sessionmaker, care, uuid4(), body="а" * 20_000)

    assert len(await _jobs(sessionmaker)) == 2


async def test_statuses_follow_both_stages(
    sessionmaker: async_sessionmaker[AsyncSession], care: Care
) -> None:
    sources = [uuid4(), uuid4(), uuid4(), uuid4()]
    ids = [await _submit(sessionmaker, care, s) for s in sources]
    now = datetime.now(UTC)
    async with sessionmaker() as session:
        # Только проверка риска — ещё «обрабатывается».
        await session.execute(
            update(Analysis)
            .where(Analysis.id == ids[1])
            .values(risk_checked_at=now, risk_run_id=uuid4())
        )
        await session.execute(
            update(Analysis)
            .where(Analysis.id == ids[2])
            .values(
                risk_checked_at=now, risk_run_id=uuid4(), extracted_at=now, extract_run_id=uuid4()
            )
        )
        await session.execute(
            update(Analysis)
            .where(Analysis.id == ids[3])
            .values(risk_checked_at=now, risk_run_id=uuid4(), extract_failed_at=now)
        )
        await session.commit()

    async with sessionmaker() as session:
        statuses = await freetext.statuses(
            session, source_type="diary", source_ids=[*sources, uuid4()]
        )

    assert statuses == {
        sources[0]: "processing",
        sources[1]: "processing",
        sources[2]: "done",
        sources[3]: "failed",
    }


def _payload(analysis_id: UUID, category: str) -> dict[str, Any]:
    return {
        "analysis_id": str(analysis_id),
        "source_type": "diary",
        "source_id": str(uuid4()),
        "category": category,
        "summary": "плохой сон",
        "quote": "Плохо сплю.",
        "drug": None,
        "adherence_event": None,
    }


async def _decide(
    maker: async_sessionmaker[AsyncSession],
    care: Care,
    analysis_id: UUID,
    category: str,
    *,
    confirm: bool,
) -> UUID:
    registry = ApplierRegistry()
    freetext.register_appliers(registry)
    async with maker() as session:
        proposal = await proposals.create_proposal(
            session,
            patient_id=care.patient.id,
            kind="freetext.finding",
            payload=_payload(analysis_id, category),
            origin_type="ai_run",
            origin_ref=str(uuid4()),
        )
        await proposals.decide(
            session,
            registry,
            doctor_id=care.doctor.id,
            proposal_id=proposal.id,
            confirm=confirm,
            comment=None,
        )
        await session.commit()
    return proposal.id


async def test_confirmed_finding_is_listed_and_rejected_is_not(
    sessionmaker: async_sessionmaker[AsyncSession], care: Care
) -> None:
    analysis_id = await _submit(sessionmaker, care, uuid4())
    confirmed = await _decide(sessionmaker, care, analysis_id, "symptom", confirm=True)
    await _decide(sessionmaker, care, analysis_id, "symptom", confirm=False)

    async with sessionmaker() as session:
        [finding] = await freetext.list_confirmed(session, patient_id=care.patient.id)

    assert finding.proposal_id == confirmed
    assert (finding.analysis_id, finding.category, finding.confirmed_by) == (
        analysis_id,
        "symptom",
        care.doctor.id,
    )
    assert finding.data == {
        **_payload(analysis_id, "symptom"),
        "source_id": finding.data["source_id"],
    }


async def test_list_confirmed_filters_by_category_and_date(
    sessionmaker: async_sessionmaker[AsyncSession], care: Care
) -> None:
    analysis_id = await _submit(sessionmaker, care, uuid4())
    await _decide(sessionmaker, care, analysis_id, "symptom", confirm=True)
    adherence = await _decide(sessionmaker, care, analysis_id, "adherence", confirm=True)

    async with sessionmaker() as session:
        by_category = await freetext.list_confirmed(
            session, patient_id=care.patient.id, categories=["adherence"]
        )
        future = await freetext.list_confirmed(
            session, patient_id=care.patient.id, since=datetime.now(UTC) + timedelta(minutes=1)
        )

    assert [f.proposal_id for f in by_category] == [adherence]
    assert future == []


async def test_written_at_gives_recording_time_or_analysis_creation_time(
    sessionmaker: async_sessionmaker[AsyncSession], care: Care
) -> None:
    typed = await _submit(sessionmaker, care, uuid4())
    recorded = datetime(2026, 10, 19, 8, 29, 50, tzinfo=UTC)
    async with sessionmaker() as session:
        # Голосовое: разбор ставится после расшифровки, время записи — раньше.
        voice = await freetext.submit_text(
            session,
            patient_id=care.patient.id,
            source_type="message",
            source_id=uuid4(),
            text="Плохо сплю.",
            transcript=True,
            recorded_at=recorded,
        )
        await session.commit()
    async with sessionmaker() as session:
        created = dict(
            (await session.execute(text("SELECT id, created_at FROM freetext.analyses"))).all()
        )

        written = await freetext.written_at(session, [typed, voice, uuid4()])

    assert written == {typed: created[typed], voice: recorded}
