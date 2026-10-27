"""Помощники тестов справки по КР: строки `therapy.guidance`, задачи очереди, задача на `Script`."""

from typing import Any

from clin_compass_knowledge.loader import load_knowledge
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.core.ai.llm import Llm
from clin_compass.core.ai.runs import DbRunRecorder
from clin_compass.modules.knowledge import service as knowledge
from clin_compass.modules.therapy.tasks import Jobs
from tests.freetext.helpers import Script
from tests.knowledge.helpers import Piece, add_document, words_embedder
from tests.therapy.conftest import KNOWLEDGE, Care

ENTRYPOINT = "therapy.guidance"
SOURCES = load_knowledge(KNOWLEDGE).sources
GUIDELINE_TITLE = SOURCES["test-guideline"].title
RECOMMENDATION = Piece(
    "Пациентам с генерализованным тревожным расстройством рекомендуется когнитивно-поведенческая "
    "терапия. Рекомендуется назначение антидепрессантов.",
    kind="recommendation",
    uur="A",
    udd=1,
)
# Утверждение по первой части фрагмента КР: цитату вырезает код по номерам частей.
CLAIM = {
    "statement": "Рекомендуется когнитивно-поведенческая терапия.",
    "fragment": "F1",
    "first": 1,
    "last": 1,
}
ANSWER: dict[str, Any] = {"claims": [CLAIM]}


async def guidance(care: Care) -> dict[str, Any]:
    response = await care.client.get(f"{care.base}/therapy/guidance")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


async def rows(maker: async_sessionmaker[AsyncSession]) -> list[dict[str, Any]]:
    """Строки справки по порядку создания."""
    async with maker() as session:
        result = await session.execute(
            text(
                "SELECT patient_id, profile_key, profile, status, answer, run_id, failures,"
                " computed_at FROM therapy.guidance ORDER BY created_at, id"
            )
        )
        return [dict(r._mapping) for r in result]


async def jobs(maker: async_sessionmaker[AsyncSession]) -> list[tuple[str, str, str]]:
    """Задачи справки в очереди: аргумент, ключ дедупликации и статус."""
    async with maker() as session:
        result = await session.execute(
            text(
                "SELECT payload, dedupe_key, status::text FROM pgq.pgqueuer"
                " WHERE entrypoint = :e ORDER BY id"
            ),
            {"e": ENTRYPOINT},
        )
        return [(bytes(p).decode(), d, s) for p, d, s in result]


async def finish_jobs(maker: async_sessionmaker[AsyncSession]) -> None:
    """Задачи выполнены: PgQueuer убирает их из очереди, ключ дедупликации свободен."""
    async with maker() as session:
        await session.execute(
            text("DELETE FROM pgq.pgqueuer WHERE entrypoint = :e"), {"e": ENTRYPOINT}
        )
        await session.commit()


async def index_sources(maker: async_sessionmaker[AsyncSession]) -> None:
    """В индексе — КР с одним пунктом рекомендации: в промпте он единственный, метка F1."""
    async with maker() as session:
        await add_document(
            session, "test-guideline", [RECOMMENDATION], title=GUIDELINE_TITLE, version="1"
        )
        await session.commit()


def make_jobs(
    maker: async_sessionmaker[AsyncSession], *answers: dict[str, Any] | Exception
) -> tuple[Jobs, Script]:
    """Задача справки: модель отвечает `answers` по очереди, вызовы пишутся в ai_runs."""
    script = Script(*answers)
    embedder, _ = words_embedder()
    ai = knowledge.KnowledgeAi(embedder, Llm(script.model, DbRunRecorder(maker)))
    return Jobs(maker, ai, SOURCES), script
