"""Поиск и чтение базы знаний (спека #27, раздел 6): RRF, фильтр источников, только `active`."""

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.modules.knowledge import service
from clin_compass.modules.knowledge.search import fuse
from tests.knowledge.helpers import Piece, add_document, words_embedder

INSOMNIA = Piece("Бессонница у пожилых пациентов требует осторожного выбора снотворных.")
ANXIETY = Piece("Генерализованная тревога лечится психотерапией.", section="doc_4")


def test_fuse_sums_reciprocal_ranks_of_both_lists() -> None:
    a, b, c = uuid4(), uuid4(), uuid4()

    fused = fuse([a, b], [c, a])

    assert [f.id for f in fused] == [a, c, b]
    assert fused[0].score == pytest.approx(1 / 61 + 1 / 62)
    assert (fused[0].vector_rank, fused[0].text_rank) == (1, 2)
    assert (fused[1].vector_rank, fused[1].text_rank) == (None, 1)
    assert fused[2].score == pytest.approx(1 / 62)


def test_fuse_breaks_ties_by_best_rank() -> None:
    a, b = uuid4(), uuid4()

    fused = fuse([a], [b])

    # Равный балл 1/61: порядок не зависит от случайного порядка множества.
    assert [f.id for f in fused] == [a, b]


async def test_search_finds_fragment_by_any_lemma_of_question(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    embedder, model = words_embedder()
    async with sessionmaker() as session:
        await add_document(session, "kr-test", [INSOMNIA, ANXIETY])

        hits = await service.search(session, embedder, "снотворное при бессоннице")

    by_text = {h.fragment.text: h for h in hits}
    assert by_text[INSOMNIA.text].text_rank == 1
    assert by_text[ANXIETY.text].text_rank is None
    assert by_text[ANXIETY.text].vector_rank is not None
    assert hits[0].fragment.text == INSOMNIA.text
    # Запрос уходит в модель как запрос: префикс инструкции ставит клиент core/ai.
    assert model.calls == [("query", ["снотворное при бессоннице"])]


async def test_hit_carries_document_and_section(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    embedder, _ = words_embedder()
    async with sessionmaker() as session:
        _, [fragment] = await add_document(
            session, "kr-test", [INSOMNIA], title="Депрессия", version="301_3"
        )

        [hit] = await service.search(session, embedder, "бессонница")

    assert hit.fragment.id == fragment.id
    assert hit.fragment.key == "kr-test/doc_3/1"
    assert hit.fragment.document.source_id == "kr-test"
    assert hit.fragment.document.title == "Депрессия"
    assert hit.fragment.document.version == "301_3"
    assert hit.fragment.section_title == "Лечение"
    assert hit.score == pytest.approx(1 / 61 + 1 / 61)


async def test_search_returns_k_best(sessionmaker: async_sessionmaker[AsyncSession]) -> None:
    embedder, _ = words_embedder()
    pieces = [Piece(f"Бессонница, вариант {n}.") for n in range(5)]
    async with sessionmaker() as session:
        await add_document(session, "kr-test", pieces)

        hits = await service.search(session, embedder, "бессонница", k=3)

    assert len(hits) == 3
    assert [h.score for h in hits] == sorted((h.score for h in hits), reverse=True)


async def test_source_filter_narrows_both_searches(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    embedder, _ = words_embedder()
    async with sessionmaker() as session:
        await add_document(session, "kr-test", [INSOMNIA])
        await add_document(session, "aviandr-ohlp", [Piece("Бессонница — нежелательная реакция.")])

        hits = await service.search(session, embedder, "бессонница", source_ids=["aviandr-ohlp"])

    assert [h.fragment.document.source_id for h in hits] == ["aviandr-ohlp"]


async def test_superseded_fragments_are_not_found(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    embedder, _ = words_embedder()
    async with sessionmaker() as session:
        await add_document(session, "kr-test", [INSOMNIA], status="superseded", version="1")
        await add_document(session, "kr-test", [ANXIETY], version="2")

        hits = await service.search(session, embedder, "бессонница")

    assert [h.fragment.document.version for h in hits] == ["2"]


async def test_question_of_stop_words_falls_back_to_vector_search(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    embedder, _ = words_embedder()
    async with sessionmaker() as session:
        await add_document(session, "kr-test", [INSOMNIA])

        [hit] = await service.search(session, embedder, "и или но")

    assert (hit.vector_rank, hit.text_rank) == (1, None)


async def test_read_section_returns_active_fragments_in_order(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    section = [Piece(f"Противопоказание {n}.", section="4.3") for n in range(1, 12)]
    async with sessionmaker() as session:
        await add_document(
            session, "aviandr-ohlp", [Piece("Старое.", section="4.3")], status="superseded"
        )
        await add_document(session, "aviandr-ohlp", [Piece("Показания.", section="4.1"), *section])

        fragments = await service.read_section(session, "aviandr-ohlp", "4.3")

    assert [f.text for f in fragments] == [p.text for p in section]
    assert fragments[-1].key == "aviandr-ohlp/4.3/11"


async def test_get_fragment_by_id(sessionmaker: async_sessionmaker[AsyncSession]) -> None:
    async with sessionmaker() as session:
        _, [fragment] = await add_document(
            session, "kr-test", [Piece("Рекомендуется.", kind="recommendation", uur="A", udd=2)]
        )

        found = await service.get_fragment(session, fragment.id)
        missing = await service.get_fragment(session, uuid4())

    assert found is not None
    assert (found.kind, found.uur, found.udd) == ("recommendation", "A", 2)
    assert found.document.url == "https://example.org/kr-test"
    assert missing is None


async def test_list_fragments_of_active_documents_in_order(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with sessionmaker() as session:
        await add_document(session, "kr-test", [Piece("Старое.")], status="superseded")
        await add_document(session, "kr-test", [INSOMNIA, ANXIETY])
        await add_document(session, "aviandr-ohlp", [Piece("Показания.", section="4.1")])

        everything = await service.list_fragments(session)
        guideline = await service.list_fragments(session, source_ids=["kr-test"])

    assert [f.key for f in everything] == [
        "aviandr-ohlp/4.1/1",
        "kr-test/doc_3/1",
        "kr-test/doc_4/1",
    ]
    assert [f.text for f in guideline] == [INSOMNIA.text, ANXIETY.text]


async def test_list_documents_shows_active_versions_with_hash(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with sessionmaker() as session:
        await add_document(session, "kr-test", status="superseded", version="1")
        await add_document(session, "kr-test", version="2", content_hash="a" * 64)

        documents = await service.list_documents(session)

    assert [(d.source_id, d.version, d.content_hash) for d in documents] == [
        ("kr-test", "2", "a" * 64)
    ]


async def test_fragment_of_superseded_version_shows_its_status(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with sessionmaker() as session:
        _, [old] = await add_document(session, "kr-test", [INSOMNIA], status="superseded")
        _, [new] = await add_document(session, "kr-test", [INSOMNIA], version="2")

        found_old = await service.get_fragment(session, old.id)
        found_new = await service.get_fragment(session, new.id)

    assert found_old is not None and found_old.document.status == "superseded"
    assert found_new is not None and found_new.document.status == "active"


async def test_hnsw_index_path_gives_same_hits_and_skips_superseded(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    # На десятке строк планировщик просматривает таблицу и сортирует. Здесь полный просмотр
    # и сортировка запрещены, и векторный поиск идёт по индексу HNSW. Тексты прежней версии
    # с теми же векторами в выдачу не попадают.
    embedder, _ = words_embedder()
    pieces = [Piece(f"Бессонница, вариант {n}.") for n in range(6)]
    async with sessionmaker() as session:
        await add_document(session, "kr-test", pieces, status="superseded", version="1")
        await add_document(session, "kr-test", pieces, version="2")
        await session.commit()
    async with sessionmaker() as session:
        plain = await service.search(session, embedder, "бессонница вариант", k=10)
    async with sessionmaker() as session:
        await session.execute(text("SET LOCAL enable_seqscan = off"))
        await session.execute(text("SET LOCAL enable_sort = off"))
        # asyncpg готовит запросы заранее. После пяти вызовов на соединении Postgres берёт
        # общий план из кэша, и смена enable_* на него не влияет. force_custom_plan строит
        # план заново при каждом вызове.
        await session.execute(text("SET LOCAL plan_cache_mode = force_custom_plan"))
        indexed = await service.search(session, embedder, "бессонница вариант", k=10)
        index_scans = await session.scalar(
            text(
                "SELECT pg_stat_get_xact_numscans("
                "'knowledge.ix_knowledge_fragments_embedding'::regclass)"
            )
        )

    assert index_scans is not None and index_scans >= 1
    assert [(h.fragment.key, h.vector_rank, h.text_rank) for h in indexed] == [
        (h.fragment.key, h.vector_rank, h.text_rank) for h in plain
    ]
    assert len(indexed) == 6
    assert {h.fragment.document.version for h in indexed} == {"2"}
