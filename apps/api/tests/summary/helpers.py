"""Помощники тестов сводки: врач с пациентом, данные периода, строки, задачи, задача на `Script`."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.core.ai.llm import Llm
from clin_compass.core.ai.runs import DbRunRecorder
from clin_compass.modules.assessments.models import AssessmentResult
from clin_compass.modules.freetext import service as freetext
from clin_compass.modules.identity.schemas import UserOut
from clin_compass.modules.proposals import service as proposals
from clin_compass.modules.proposals.service import ApplierRegistry
from clin_compass.modules.summary.drafting import Drafter
from clin_compass.modules.summary.tasks import Jobs
from tests.freetext.helpers import Script

ENTRYPOINT = "summary.draft"
MOSCOW = ZoneInfo("Europe/Moscow")
SLEEP = "Дневник, симптом: плохой сон. Цитата: «Плохо сплю.»"
STATEMENT = {"text": "Пациент отметил плохой сон.", "facts": [1]}
ANSWER: dict[str, Any] = {"statements": [STATEMENT]}
_SOURCE = {
    "id": "zolotareva-gad7-2023",
    "locator": "с. 35",
    "title": "Источник порогов",
    "url": "https://example.org/source",
    "supplementary": False,
}


@dataclass(frozen=True)
class SummaryCare:
    """Врач вошёл в API; у него пациент."""

    client: httpx.AsyncClient
    doctor: UserOut
    patient_id: UUID

    @property
    def base(self) -> str:
        return f"/api/v1/patients/{self.patient_id}/summary"


async def get_summary(care: SummaryCare) -> dict[str, Any]:
    response = await care.client.get(care.base)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


async def confirm(
    care: SummaryCare, summary_id: str, statements: list[dict[str, Any]]
) -> httpx.Response:
    return await care.client.post(
        f"{care.base}/{summary_id}/confirm", json={"statements": statements}
    )


async def add_finding(
    maker: async_sessionmaker[AsyncSession],
    care: SummaryCare,
    *,
    summary: str = "плохой сон",
    quote: str = "Плохо сплю.",
    category: str = "symptom",
) -> None:
    """Запись дневника на разборе и подтверждённая врачом находка из неё."""
    registry = ApplierRegistry()
    freetext.register_appliers(registry)
    async with maker() as session:
        analysis_id = await freetext.submit_text(
            session,
            patient_id=care.patient_id,
            source_type="diary",
            source_id=uuid4(),
            text=quote,
            transcript=False,
        )
        proposal = await proposals.create_proposal(
            session,
            patient_id=care.patient_id,
            kind="freetext.finding",
            payload={
                "analysis_id": str(analysis_id),
                "source_type": "diary",
                "transcript": False,
                "category": category,
                "summary": summary,
                "quote": quote,
                "drug": None,
                "adherence_event": None,
            },
            origin_type="ai_run",
            origin_ref=str(uuid4()),
        )
        await proposals.decide(
            session,
            registry,
            doctor_id=care.doctor.id,
            proposal_id=proposal.id,
            confirm=True,
            comment=None,
        )
        await session.commit()


async def remove_finding(maker: async_sessionmaker[AsyncSession], quote: str) -> None:
    """Убрать подтверждённую находку, чтобы данные периода вернулись к прежним.

    В приложении находки не удаляются (триггер БД); тест обходит триггер, как очистка БД.
    """
    async with maker() as session:
        await session.execute(text("SET LOCAL session_replication_role = replica"))
        await session.execute(
            text("DELETE FROM freetext.findings WHERE data->>'quote' = :quote"), {"quote": quote}
        )
        await session.commit()


async def add_result(
    maker: async_sessionmaker[AsyncSession], care: SummaryCare, value: int, band: str
) -> None:
    """Результат PHQ-9, заполненный сейчас."""
    async with maker() as session:
        session.add(
            AssessmentResult(
                patient_id=care.patient_id,
                submission_id=uuid4(),
                scale_id="phq-9",
                scale_version=1,
                scale_title="Опросник здоровья пациента (PHQ-9)",
                completed_at=datetime.now(UTC),
                answers={},
                scores={
                    "total": {
                        "value": value,
                        "band": {"code": "b", "label": band, "source": _SOURCE},
                    }
                },
                definition_snapshot={},
                sources_snapshot=[],
            )
        )
        await session.commit()


async def rows(maker: async_sessionmaker[AsyncSession]) -> list[dict[str, Any]]:
    """Строки сводок по порядку создания."""
    async with maker() as session:
        result = await session.execute(
            text("SELECT * FROM summary.visit_summaries ORDER BY created_at, id")
        )
        return [dict(r._mapping) for r in result]


async def jobs(maker: async_sessionmaker[AsyncSession]) -> list[tuple[str, str]]:
    """Задачи черновика в очереди: аргумент и ключ дедупликации."""
    async with maker() as session:
        result = await session.execute(
            text("SELECT payload, dedupe_key FROM pgq.pgqueuer WHERE entrypoint = :e ORDER BY id"),
            {"e": ENTRYPOINT},
        )
        return [(bytes(p).decode(), d) for p, d in result]


async def finish_jobs(maker: async_sessionmaker[AsyncSession]) -> None:
    """Задачи выполнены: PgQueuer убирает их из очереди, ключ дедупликации свободен."""
    async with maker() as session:
        await session.execute(
            text("DELETE FROM pgq.pgqueuer WHERE entrypoint = :e"), {"e": ENTRYPOINT}
        )
        await session.commit()


def make_jobs(
    maker: async_sessionmaker[AsyncSession], *answers: dict[str, Any] | Exception
) -> tuple[Jobs, Script]:
    """Задача черновика: модель отвечает `answers` по очереди, вызовы пишутся в ai_runs."""
    script = Script(*answers)
    drafter = Drafter(Llm(script.model, DbRunRecorder(maker)), MOSCOW)
    return Jobs(maker, drafter, MOSCOW), script


async def drafted(
    maker: async_sessionmaker[AsyncSession], care: SummaryCare, answer: dict[str, Any] = ANSWER
) -> dict[str, Any]:
    """GET поставил расчёт, задача его выполнила; возвращает готовую сводку."""
    await get_summary(care)
    [(payload, _)] = await jobs(maker)
    jobs_, _ = make_jobs(maker, answer)
    await jobs_.draft(payload.encode())
    await finish_jobs(maker)
    return await get_summary(care)
