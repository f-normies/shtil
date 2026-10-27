"""Сводка индекса для health и выгрузки (спека #109, 4.3): версия и счётчики."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.modules.knowledge import service
from clin_compass.modules.knowledge.schemas import IndexSummary
from clin_compass.modules.knowledge.transfer import index_version
from tests.knowledge.helpers import Piece, add_document


def test_version_does_not_depend_on_document_order() -> None:
    forward = index_version([("kr-a", "a" * 64), ("kr-b", "b" * 64)])
    backward = index_version([("kr-b", "b" * 64), ("kr-a", "a" * 64)])

    assert forward == backward
    assert len(forward or "") == 12


def test_version_changes_with_content_hash() -> None:
    before = index_version([("kr-a", "a" * 64), ("kr-b", "b" * 64)])
    after = index_version([("kr-a", "a" * 64), ("kr-b", "c" * 64)])

    assert before != after


async def test_empty_index_has_no_version(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    assert await service.index_summary(sessionmaker) == IndexSummary(
        version=None, documents=0, fragments=0
    )


async def test_summary_counts_only_active_documents_and_their_fragments(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with sessionmaker() as session:
        await add_document(
            session,
            "kr-a",
            [Piece("Прежний."), Piece("Прежний 2.")],
            status="superseded",
            content_hash="0" * 64,
        )
        await add_document(
            session, "kr-a", [Piece("Первый."), Piece("Второй.")], content_hash="a" * 64
        )
        await add_document(session, "kr-b", [Piece("Третий.")], content_hash="b" * 64)
        await session.commit()

    summary = await service.index_summary(sessionmaker)

    assert summary == IndexSummary(
        version=index_version([("kr-a", "a" * 64), ("kr-b", "b" * 64)]), documents=2, fragments=3
    )
