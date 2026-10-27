"""Таблицы базы знаний (спека #27, раздел 4): ограничения, halfvec(2048), tsvector с `russian`."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.knowledge.helpers import DIMENSIONS, Piece, add_document, words_vector

# Копия фрагмента в тот же документ с тем же ключом.
_COPY_FRAGMENT = text(
    "INSERT INTO knowledge.fragments (document_id, position, key, section, section_title, "
    "heading_path, kind, text, embedding) "
    "SELECT document_id, position + 1, key, section, section_title, heading_path, kind, text, "
    "embedding FROM knowledge.fragments WHERE id = :id"
)


async def test_second_active_document_of_one_source_is_rejected(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with sessionmaker() as session:
        await add_document(session, "kr-test")
        with pytest.raises(IntegrityError, match="uq_documents_active_source_id"):
            await add_document(session, "kr-test")


async def test_superseded_versions_stay_next_to_active_one(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with sessionmaker() as session:
        await add_document(session, "kr-test", status="superseded", version="1")
        await add_document(session, "kr-test", status="superseded", version="2")
        await add_document(session, "kr-test", version="3")
        await session.commit()


async def test_fragment_key_is_unique_within_document_only(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with sessionmaker() as session:
        # Тот же ключ в прежней версии документа допустим.
        await add_document(session, "kr-test", [Piece("Первый.")], status="superseded")
        _, [fragment] = await add_document(session, "kr-test", [Piece("Первый.")])
        await session.commit()
        with pytest.raises(IntegrityError, match="uq_fragments_document_id"):
            await session.execute(_COPY_FRAGMENT, {"id": fragment.id})


async def test_only_recommendation_carries_evidence_levels(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with sessionmaker() as session:
        await add_document(
            session, "kr-test", [Piece("Рекомендуется.", kind="recommendation", uur="A", udd=1)]
        )
        await session.commit()
        with pytest.raises(IntegrityError, match="ck_fragments_levels"):
            await add_document(session, "kr-other", [Piece("Текст.", uur="A", udd=1)])


async def test_tsv_holds_russian_lemmas_of_heading_and_text(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with sessionmaker() as session:
        await add_document(
            session,
            "kr-test",
            [Piece("Пациентам рекомендуется назначать антидепрессанты.", heading_path="Терапия")],
        )
        matches = await session.scalar(
            text(
                "SELECT count(*) FROM knowledge.fragments "
                "WHERE tsv @@ plainto_tsquery('russian', 'антидепрессантов терапии')"
            )
        )

    assert matches == 1


async def test_embedding_is_stored_as_halfvec_2048(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    vector = words_vector("сон тревога")
    async with sessionmaker() as session:
        await add_document(session, "kr-test", [Piece("Сон.", embedding=vector)])
        column_type = await session.scalar(
            text(
                "SELECT format_type(atttypid, atttypmod) FROM pg_attribute "
                "WHERE attrelid = 'knowledge.fragments'::regclass AND attname = 'embedding'"
            )
        )
        distance = await session.scalar(
            text("SELECT embedding <=> CAST(:v AS halfvec) FROM knowledge.fragments"),
            {"v": str(vector)},
        )

    assert column_type == f"halfvec({DIMENSIONS})"
    assert distance == pytest.approx(0.0, abs=1e-3)


async def test_search_indexes_exist(sessionmaker: async_sessionmaker[AsyncSession]) -> None:
    async with sessionmaker() as session:
        definitions = list(
            await session.scalars(
                text("SELECT indexdef FROM pg_indexes WHERE schemaname = 'knowledge'")
            )
        )

    assert any("USING hnsw (embedding halfvec_cosine_ops)" in d for d in definitions)
    assert any("USING gin (tsv)" in d for d in definitions)
