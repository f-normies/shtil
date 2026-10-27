"""Публичный интерфейс модуля freetext (спека #32, раздел 3.1).

Дневник (#31) и чат (#33) отдают текст пациента через `submit_text` в своей транзакции.
Разбор идёт в worker'е двумя задачами: быстрая проверка риска и полный разбор (`tasks.py`).
Находки становятся предложениями врачу; подтверждённые читают приверженность, безопасность
терапии и сводка через `list_confirmed`.
"""

from collections.abc import Collection, Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from clin_compass.core import queue
from clin_compass.modules.freetext.models import Analysis, Finding
from clin_compass.modules.freetext.schemas import (
    AnalysisStatus,
    DiarySourceOut,
    FindingCategory,
    FindingOut,
    SourceType,
)
from clin_compass.modules.proposals import service as proposals_service
from clin_compass.modules.proposals.schemas import ProposalOut
from clin_compass.modules.proposals.service import ApplierRegistry

PROPOSAL_KIND = "freetext.finding"
RISK_ENTRYPOINT = "freetext.check_risk"
EXTRACT_ENTRYPOINT = "freetext.extract"
# ~5 тыс. токенов: с промптом и ответом укладывается в --max-model-len 16384 узла LLM.
MAX_TEXT_LENGTH = 20_000
# Проверка риска выбирается из очереди раньше полных разборов.
_RISK_PRIORITY = 10


async def submit_text(
    session: AsyncSession,
    *,
    patient_id: UUID,
    source_type: SourceType,
    source_id: UUID,
    text: str,
    transcript: bool,
    recorded_at: datetime | None = None,
) -> UUID:
    """Поставить текст пациента на разбор в транзакции вызывающего. Коммитит вызывающий.

    Повторный вызов для того же источника возвращает id существующего разбора и задач
    не ставит; текст при этом не сравнивается. `transcript` — текст получен из голосовой.
    `recorded_at` — время записи, если разбор ставится позже неё (голосовое сообщение после
    расшифровки). Без него время записи — время разбора (спека #96, раздел 3.3).

    Raises:
        ValueError: Текст пустой или длиннее `MAX_TEXT_LENGTH` символов.
    """
    if not text.strip() or len(text) > MAX_TEXT_LENGTH:
        raise ValueError(f"text must be non-empty and at most {MAX_TEXT_LENGTH} characters")
    created: UUID | None = await session.scalar(
        insert(Analysis)
        .values(
            patient_id=patient_id,
            source_type=source_type,
            source_id=source_id,
            transcript=transcript,
            text=text,
            recorded_at=recorded_at,
        )
        .on_conflict_do_nothing(index_elements=["source_type", "source_id"])
        .returning(Analysis.id)
    )
    if created is None:
        existing: UUID | None = await session.scalar(
            select(Analysis.id).where(
                Analysis.source_type == source_type, Analysis.source_id == source_id
            )
        )
        if existing is None:
            raise RuntimeError("analysis conflict without an existing row")
        return existing
    payload = str(created).encode()
    await queue.enqueue(
        session,
        RISK_ENTRYPOINT,
        payload,
        priority=_RISK_PRIORITY,
        dedupe_key=f"{RISK_ENTRYPOINT}:{created}",
    )
    await queue.enqueue(
        session, EXTRACT_ENTRYPOINT, payload, dedupe_key=f"{EXTRACT_ENTRYPOINT}:{created}"
    )
    return created


async def statuses(
    session: AsyncSession, *, source_type: SourceType, source_ids: Collection[UUID]
) -> dict[UUID, AnalysisStatus]:
    """Статус разбора по источникам. Источника, который не ставили на разбор, в ответе нет."""
    rows = await session.scalars(
        select(Analysis).where(
            Analysis.source_type == source_type, Analysis.source_id.in_(list(source_ids))
        )
    )
    return {a.source_id: status_of(a) for a in rows}


async def list_diary_sources(
    session: AsyncSession,
    *,
    patient_id: UUID,
    source_ids: Collection[UUID],
) -> dict[UUID, "DiarySourceOut"]:
    """Пакетно прочитать разборы, открытые предложения и подтверждения для дневника."""
    if not source_ids:
        return {}
    analyses = list(
        await session.scalars(
            select(Analysis).where(
                Analysis.patient_id == patient_id,
                Analysis.source_type == "diary",
                Analysis.source_id.in_(list(source_ids)),
            )
        )
    )
    if not analyses:
        return {}
    by_analysis_id = {analysis.id: analysis for analysis in analyses}
    analysis_ids = set(by_analysis_id)
    proposal_rows = await proposals_service.list_open(
        session, patient_id=patient_id, kind=PROPOSAL_KIND
    )
    proposals_by_analysis: dict[UUID, list[ProposalOut]] = {}
    for proposal in proposal_rows:
        try:
            analysis_id = UUID(proposal.payload.get("analysis_id", ""))
        except (KeyError, TypeError, ValueError):
            continue
        if analysis_id in analysis_ids:
            proposals_by_analysis.setdefault(analysis_id, []).append(proposal)
    finding_rows = await session.scalars(
        select(Finding).where(
            Finding.patient_id == patient_id, Finding.analysis_id.in_(analysis_ids)
        )
    )
    findings_by_analysis: dict[UUID, list[FindingOut]] = {}
    for finding in finding_rows:
        findings_by_analysis.setdefault(finding.analysis_id, []).append(
            FindingOut.model_validate(finding)
        )

    return {
        analysis.source_id: DiarySourceOut(
            source_id=analysis.source_id,
            status=status_of(analysis),
            proposals=proposals_by_analysis.get(analysis.id, []),
            findings=findings_by_analysis.get(analysis.id, []),
        )
        for analysis in analyses
    }


def status_of(analysis: Analysis) -> AnalysisStatus:
    """`failed`, если этап исчерпал попытки; `done`, если оба этапа завершены."""
    if analysis.risk_failed_at is not None or analysis.extract_failed_at is not None:
        return "failed"
    if analysis.risk_checked_at is not None and analysis.extracted_at is not None:
        return "done"
    return "processing"


async def list_confirmed(
    session: AsyncSession,
    *,
    patient_id: UUID,
    categories: Sequence[FindingCategory] | None = None,
    since: datetime | None = None,
) -> list[FindingOut]:
    """Подтверждённые находки пациента по времени подтверждения.

    Право доступа проверяет вызывающий (`patients.deps.owned_patient`).
    """
    query = select(Finding).where(Finding.patient_id == patient_id)
    if categories is not None:
        query = query.where(Finding.category.in_(list(categories)))
    if since is not None:
        query = query.where(Finding.confirmed_at >= since)
    rows = await session.scalars(query.order_by(Finding.confirmed_at, Finding.id))
    return [FindingOut.model_validate(f) for f in rows]


async def written_at(session: AsyncSession, analysis_ids: Collection[UUID]) -> dict[UUID, datetime]:
    """Когда пациент записал текст разбора, по id разбора. Неизвестных id в ответе нет.

    Время голосовой записи (`recorded_at`, #96), иначе время постановки разбора: голосовое
    разбирается после расшифровки. Находка подтверждается позже: сводка (#38) датирует её
    временем записи пациента.
    """
    written = func.coalesce(Analysis.recorded_at, Analysis.created_at)
    rows = await session.execute(
        select(Analysis.id, written).where(Analysis.id.in_(list(analysis_ids)))
    )
    return {analysis_id: created for analysis_id, created in rows}


def register_appliers(registry: ApplierRegistry) -> None:
    """Applier находок: без него `proposals.decide` не даст врачу подтвердить находку."""
    registry.register(PROPOSAL_KIND, _apply_finding)


async def _apply_finding(session: AsyncSession, proposal: ProposalOut) -> None:
    """Подтверждённая находка — в `freetext.findings`, в транзакции решения врача."""
    if proposal.decided_by is None or proposal.decided_at is None:
        raise RuntimeError("finding applied without a doctor's decision")
    session.add(
        Finding(
            proposal_id=proposal.id,
            analysis_id=UUID(proposal.payload["analysis_id"]),
            patient_id=proposal.patient_id,
            category=proposal.payload["category"],
            data=proposal.payload,
            confirmed_at=proposal.decided_at,
            confirmed_by=proposal.decided_by,
        )
    )
    await session.flush()
