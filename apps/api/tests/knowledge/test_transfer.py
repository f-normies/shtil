"""Перенос индекса базы знаний между базами (спека #109, 4.1–4.2).

Выгрузка берёт действующие документы с фрагментами и векторами. Загрузка сливает их
с индексом базы одной транзакцией и отказывает при несовместимом файле.
"""

import asyncio
import json
from collections.abc import Callable
from typing import Any
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import func, make_url, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.core.db import make_engine
from clin_compass.modules.knowledge import service
from clin_compass.modules.knowledge.models import Document
from clin_compass.modules.knowledge.transfer import KNOWLEDGE_SCHEMA_REVISION
from tests.conftest import API_DIR
from tests.knowledge.helpers import Piece, add_document

SCHEMA = KNOWLEDGE_SCHEMA_REVISION
OLD_HASH = "1" * 64
KR_HASH = "a" * 64
OHLP_HASH = "b" * 64
KR_PIECES = [
    Piece("Пациентам рекомендуется сон.", kind="recommendation", uur="A", udd=1),
    Piece("Дозы препаратов приведены в таблице.", kind="table", section="doc_4"),
]
OHLP_PIECES = [Piece("Противопоказания: беременность.", section="4.3", heading_path="4.3")]

DOCUMENTS = text(
    "SELECT id, source_id, version, title, url, kind, content_hash, status, created_at, "
    "superseded_at FROM knowledge.documents ORDER BY id"
)
FRAGMENTS = text(
    "SELECT id, document_id, position, key, section, section_title, heading_path, kind, text, "
    "uur, udd, embedding::text, tsv::text FROM knowledge.fragments ORDER BY id"
)


async def _state(sessionmaker: async_sessionmaker[AsyncSession]) -> tuple[list[Any], list[Any]]:
    """Все строки обеих таблиц; вектор и tsvector — в текстовом виде Postgres."""
    async with sessionmaker() as session:
        documents = [tuple(row) for row in await session.execute(DOCUMENTS)]
        fragments = [tuple(row) for row in await session.execute(FRAGMENTS)]
    return documents, fragments


async def _truncate(sessionmaker: async_sessionmaker[AsyncSession]) -> None:
    async with sessionmaker() as session:
        await session.execute(text("TRUNCATE knowledge.fragments, knowledge.documents"))
        await session.commit()


async def _seed(sessionmaker: async_sessionmaker[AsyncSession], *, kr_hash: str = KR_HASH) -> None:
    """Прежняя и действующая версии КР и действующая инструкция."""
    async with sessionmaker() as session:
        await add_document(
            session,
            "kr-test",
            [Piece("Прежний текст.")],
            status="superseded",
            version="1",
            content_hash=OLD_HASH,
        )
        await add_document(session, "kr-test", KR_PIECES, version="2", content_hash=kr_hash)
        await add_document(
            session,
            "aviandr-ohlp",
            OHLP_PIECES,
            kind="drug_label",
            title="ОХЛП Авиандр",
            content_hash=OHLP_HASH,
        )
        await session.commit()


async def _export(sessionmaker: async_sessionmaker[AsyncSession]) -> bytes:
    dump = await service.export_index(sessionmaker)
    return dump.model_dump_json().encode()


async def _supersede_and_add(
    sessionmaker: async_sessionmaker[AsyncSession], source_id: str, content_hash: str
) -> None:
    """Новая версия источника, как её пишет `make index`."""
    async with sessionmaker() as session:
        await session.execute(
            update(Document)
            .where(Document.source_id == source_id, Document.status == "active")
            .values(status="superseded", superseded_at=func.now())
        )
        await add_document(session, source_id, [Piece("Новый текст.")], content_hash=content_hash)
        await session.commit()


async def test_export_has_active_documents_with_fragments_and_header(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await _seed(sessionmaker)

    dump = await service.export_index(sessionmaker)

    assert dump.format == 1
    assert dump.knowledge_schema == KNOWLEDGE_SCHEMA_REVISION
    assert dump.embedding_model == "giga-embeddings-3b-0826"
    assert dump.dimensions == 2048
    assert [(d.source_id, d.content_hash) for d in dump.documents] == [
        ("aviandr-ohlp", OHLP_HASH),
        ("kr-test", KR_HASH),
    ]
    kr = dump.documents[1]
    assert [(f.key, f.kind, f.text, f.uur, f.udd) for f in kr.fragments] == [
        ("kr-test/doc_3/1", "recommendation", KR_PIECES[0].text, "A", 1),
        ("kr-test/doc_4/1", "table", KR_PIECES[1].text, None, None),
    ]
    assert all(len(f.embedding) == 2048 for d in dump.documents for f in d.fragments)
    # Та же версия, что покажет health базы, куда загрузят файл.
    assert dump.version is not None
    assert dump.version == (await service.index_summary(sessionmaker)).version


async def test_export_then_import_into_empty_tables_restores_rows(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await _seed(sessionmaker)
    data = await _export(sessionmaker)
    documents, fragments = await _state(sessionmaker)
    await _truncate(sessionmaker)

    reports = await service.import_index(sessionmaker, data)

    assert [r.line() for r in reports] == [
        "aviandr-ohlp: загружен, фрагментов: 1",
        "kr-test: загружен, фрагментов: 2",
    ]
    active = [d for d in documents if d[7] == "active"]
    active_ids = {d[0] for d in active}
    assert await _state(sessionmaker) == (
        active,
        [f for f in fragments if f[1] in active_ids],
    )
    async with sessionmaker() as session:
        found = await session.scalars(
            text(
                "SELECT text FROM knowledge.fragments "
                "WHERE tsv @@ plainto_tsquery('russian', 'беременности')"
            )
        )
        assert list(found) == [OHLP_PIECES[0].text]


async def test_repeated_import_changes_nothing(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await _seed(sessionmaker)
    data = await _export(sessionmaker)
    before = await _state(sessionmaker)

    reports = await service.import_index(sessionmaker, data)

    assert [r.line() for r in reports] == [
        "aviandr-ohlp: без изменений",
        "kr-test: без изменений",
    ]
    assert await _state(sessionmaker) == before


async def test_new_version_supersedes_current_document_and_keeps_its_fragments(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await _seed(sessionmaker)
    data = await _export(sessionmaker)
    new_kr = next(d for d in json.loads(data)["documents"] if d["source_id"] == "kr-test")
    # На стенде действует прежняя версия КР с другим id.
    await _truncate(sessionmaker)
    async with sessionmaker() as session:
        stand_kr, stand_fragments = await add_document(
            session, "kr-test", [Piece("Текст стенда.")], content_hash=OLD_HASH
        )
        await session.commit()

    reports = await service.import_index(sessionmaker, data)

    assert "kr-test: загружен, фрагментов: 2" in [r.line() for r in reports]
    async with sessionmaker() as session:
        rows = await session.execute(
            text(
                "SELECT id::text, status, superseded_at IS NOT NULL FROM knowledge.documents "
                "WHERE source_id = 'kr-test' ORDER BY status"
            )
        )
        assert [tuple(row) for row in rows] == [
            (new_kr["id"], "active", False),
            (str(stand_kr.id), "superseded", True),
        ]
        kept = await session.scalar(
            text("SELECT text FROM knowledge.fragments WHERE id = :id"),
            {"id": stand_fragments[0].id},
        )
        assert kept == "Текст стенда."


async def test_previous_file_restores_previous_version(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await _seed(sessionmaker)
    previous = await _export(sessionmaker)
    await _supersede_and_add(sessionmaker, "kr-test", "c" * 64)
    current = await _export(sessionmaker)

    reports = await service.import_index(sessionmaker, previous)

    assert [r.line() for r in reports] == [
        "aviandr-ohlp: без изменений",
        "kr-test: возвращена прежняя версия",
    ]
    assert await _active_hashes(sessionmaker) == {"aviandr-ohlp": OHLP_HASH, "kr-test": KR_HASH}

    reports = await service.import_index(sessionmaker, current)

    assert "kr-test: возвращена прежняя версия" in [r.line() for r in reports]
    assert await _active_hashes(sessionmaker) == {"aviandr-ohlp": OHLP_HASH, "kr-test": "c" * 64}


async def _active_hashes(sessionmaker: async_sessionmaker[AsyncSession]) -> dict[str, str]:
    async with sessionmaker() as session:
        rows = await session.execute(
            text(
                "SELECT source_id, content_hash FROM knowledge.documents WHERE status = 'active' "
                "AND superseded_at IS NULL"
            )
        )
        return {source_id: content_hash for source_id, content_hash in rows}


def _set(key: str, value: object) -> Callable[[dict[str, Any]], None]:
    def change(dump: dict[str, Any]) -> None:
        dump[key] = value

    return change


def _other_dimensions(dump: dict[str, Any]) -> None:
    # Файл другой модели целиком согласован: и поле, и векторы.
    dump["dimensions"] = 1024
    for document in dump["documents"]:
        for fragment in document["fragments"]:
            fragment["embedding"] = fragment["embedding"][:1024]


def _no_documents_field(dump: dict[str, Any]) -> None:
    del dump["documents"]


def _other_hash(dump: dict[str, Any]) -> None:
    dump["documents"][1]["content_hash"] = "d" * 64


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (_set("knowledge_schema", "0123456789ab"), f"0123456789ab, в коде {SCHEMA}"),
        (_set("embedding_model", "other-model"), "other-model, в коде giga-embeddings-3b-0826"),
        (_other_dimensions, "1024, в коде 2048"),
        (_no_documents_field, "не по схеме"),
        (_other_hash, "kr-test"),
    ],
    ids=["schema", "model", "dimensions", "not-schema", "other-hash"],
)
async def test_incompatible_file_is_refused_without_changes(
    sessionmaker: async_sessionmaker[AsyncSession],
    change: Callable[[dict[str, Any]], None],
    message: str,
) -> None:
    await _seed(sessionmaker)
    dump = json.loads(await _export(sessionmaker))
    # Файл несёт и новый документ: при отказе его не должно быть в базе.
    dump["documents"][0]["id"] = str(uuid4())
    dump["documents"][0]["content_hash"] = "e" * 64
    for fragment in dump["documents"][0]["fragments"]:
        fragment["id"] = str(uuid4())
    change(dump)
    before = await _state(sessionmaker)

    with pytest.raises(service.TransferError, match=message):
        await service.import_index(sessionmaker, json.dumps(dump).encode())

    assert await _state(sessionmaker) == before


async def test_not_json_is_refused(sessionmaker: async_sessionmaker[AsyncSession]) -> None:
    with pytest.raises(service.TransferError, match="не по схеме"):
        await service.import_index(sessionmaker, b"not json")


async def test_other_schema_revision_is_named_even_when_fragment_columns_differ(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    # Файл другой ревизии схемы: у фрагментов есть лишняя колонка и нет одной из нынешних.
    await _seed(sessionmaker)
    dump = json.loads(await _export(sessionmaker))
    dump["knowledge_schema"] = "0123456789ab"
    for document in dump["documents"]:
        for fragment in document["fragments"]:
            fragment["page"] = 1
            del fragment["heading_path"]

    with pytest.raises(service.TransferError) as refused:
        await service.import_index(sessionmaker, json.dumps(dump).encode())

    assert str(refused.value) == (f"ревизия схемы knowledge: в файле 0123456789ab, в коде {SCHEMA}")


async def test_error_on_second_document_rolls_back_first(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await _seed(sessionmaker)
    dump = json.loads(await _export(sessionmaker))
    await _truncate(sessionmaker)
    # На стенде документ с тем же id, что у КР в файле, но с другим хешем.
    async with sessionmaker() as session:
        document, _ = await add_document(session, "kr-test", content_hash=OLD_HASH)
        await session.commit()
    dump["documents"][1]["id"] = str(document.id)
    before = await _state(sessionmaker)

    with pytest.raises(service.TransferError, match="kr-test"):
        await service.import_index(sessionmaker, json.dumps(dump).encode())

    # Инструкция шла в файле первой и была бы загружена.
    assert await _state(sessionmaker) == before


# Объекты схемы `knowledge` в каталоге Postgres: таблицы, колонки, ограничения, индексы,
# триггеры и права. Их изменение после миграции и есть изменение схемы.
_KNOWLEDGE_CATALOG = [
    "SELECT nspname, nspacl::text FROM pg_namespace WHERE nspname = 'knowledge'",
    "SELECT c.relname, c.relkind::text, c.relacl::text FROM pg_class c "
    "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'knowledge'",
    "SELECT table_name, column_name, data_type, udt_name, is_nullable, column_default, "
    "generation_expression FROM information_schema.columns WHERE table_schema = 'knowledge'",
    "SELECT conname, pg_get_constraintdef(c.oid) FROM pg_constraint c "
    "JOIN pg_namespace n ON n.oid = c.connamespace WHERE n.nspname = 'knowledge'",
    "SELECT indexdef FROM pg_indexes WHERE schemaname = 'knowledge'",
    "SELECT pg_get_triggerdef(t.oid) FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
    "JOIN pg_namespace n ON n.oid = c.relnamespace "
    "WHERE n.nspname = 'knowledge' AND NOT t.tgisinternal",
]


async def _knowledge_catalog(url: str) -> list[list[tuple[Any, ...]]]:
    engine = make_engine(url)
    try:
        async with engine.connect() as connection:
            return [
                sorted(tuple(row) for row in await connection.execute(text(query)))
                for query in _KNOWLEDGE_CATALOG
            ]
    finally:
        await engine.dispose()


async def _create_database(url: str, name: str) -> str:
    engine = make_engine(url).execution_options(isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as connection:
            await connection.execute(text(f"DROP DATABASE IF EXISTS {name}"))
            await connection.execute(text(f"CREATE DATABASE {name}"))
    finally:
        await engine.dispose()
    return make_url(url).set(database=name).render_as_string(hide_password=False)


def test_schema_revision_is_last_migration_that_changes_knowledge(postgres_url: str) -> None:
    # Миграции идут по одной в отдельной базе: общая база тестов уже на последней ревизии.
    url = asyncio.run(_create_database(postgres_url, "knowledge_revisions"))
    config = Config(str(API_DIR / "alembic.ini"))
    config.attributes["database_url"] = url
    revisions = [s.revision for s in ScriptDirectory.from_config(config).walk_revisions()]
    last_change = None
    before = asyncio.run(_knowledge_catalog(url))
    for revision in reversed(revisions):
        command.upgrade(config, revision)
        after = asyncio.run(_knowledge_catalog(url))
        if after != before:
            last_change = revision
        before = after

    assert last_change == KNOWLEDGE_SCHEMA_REVISION
