"""Перенос индекса базы знаний между базами: `knowledge-export` и `knowledge-import` (спека #109).

Выгрузка — действующие документы с фрагментами и векторами в одном JSON. Загрузка сливает
их с индексом базы одной транзакцией: при отказе или сбое база не меняется. Прежние версии
документов не удаляются: на их фрагменты ссылаются подтверждённые рекомендации.
"""

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator
from sqlalchemy import func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import undefer

from clin_compass.core.ai.embeddings import MODEL_ALIAS
from clin_compass.modules.knowledge import models
from clin_compass.modules.knowledge.fragments import FragmentKind
from clin_compass.modules.knowledge.schemas import DocumentKind, IndexSummary

# Последняя миграция, которая меняла таблицы схемы `knowledge`. Файл другой ревизии
# не загружается. Миграции других модулей её не меняют, поэтому прежний файл загружается
# и после них. Совпадение с миграциями проверяет тест.
KNOWLEDGE_SCHEMA_REVISION = "59e0bc9898f3"
# Версия формата файла выгрузки.
FORMAT = 1
# Длина версии индекса: первые знаки SHA-256.
VERSION_LENGTH = 12

ImportStatus = Literal["loaded", "unchanged", "restored"]


class TransferError(Exception):
    """Файл не загружен, база не изменилась. Текст — для вывода команды."""


class FragmentDump(BaseModel):
    """Фрагмент со всеми колонками, кроме `tsv`: его Postgres вычисляет сам."""

    model_config = ConfigDict(frozen=True, extra="forbid", from_attributes=True)

    id: UUID
    position: int
    key: str
    section: str
    section_title: str
    heading_path: str
    kind: FragmentKind
    text: str
    uur: str | None
    udd: int | None
    # Числа float16 из halfvec; каждое точно записывается в float.
    embedding: list[float]


class DocumentDump(BaseModel):
    """Действующий документ: `status` и `superseded_at` задаёт база, куда его загружают."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    source_id: str
    version: str
    title: str
    url: str
    kind: DocumentKind
    content_hash: str
    created_at: datetime
    fragments: list[FragmentDump]


class DumpHeader(BaseModel):
    """Заголовок файла. Проверяется до остального файла: у выгрузки другой ревизии схемы
    могут быть другие колонки, и отказ должен назвать ревизию."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    format: int
    knowledge_schema: str
    embedding_model: str
    dimensions: int


class IndexDump(BaseModel):
    """Файл выгрузки. Заголовок говорит, с какой схемой и моделью совместимы векторы."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    format: Literal[1]
    knowledge_schema: str
    embedding_model: str
    dimensions: int
    version: str | None
    documents: list[DocumentDump]

    @model_validator(mode="after")
    def _check_vectors(self) -> Self:
        for document in self.documents:
            for fragment in document.fragments:
                if len(fragment.embedding) != self.dimensions:
                    raise ValueError(
                        f"вектор фрагмента {fragment.key}: чисел {len(fragment.embedding)},"
                        f" в заголовке {self.dimensions}"
                    )
        return self

    def summary(self) -> IndexSummary:
        """Версия и счётчики выгрузки: их же покажет health базы после загрузки."""
        return IndexSummary(
            version=self.version,
            documents=len(self.documents),
            fragments=sum(len(document.fragments) for document in self.documents),
        )


@dataclass(frozen=True)
class ImportReport:
    """Итог загрузки по источнику: строка вывода команды."""

    source_id: str
    status: ImportStatus
    fragments: int = 0

    def line(self) -> str:
        """`<id>: загружен, фрагментов: N`, `<id>: без изменений` или прежняя версия."""
        if self.status == "loaded":
            return f"{self.source_id}: загружен, фрагментов: {self.fragments}"
        if self.status == "unchanged":
            return f"{self.source_id}: без изменений"
        return f"{self.source_id}: возвращена прежняя версия"


def index_version(documents: Iterable[tuple[str, str]]) -> str | None:
    """Версия индекса по парам `(source_id, content_hash)` действующих документов.

    Один корпус даёт одну версию в любой базе и при любом порядке документов. Пустой
    индекс — None.
    """
    lines = sorted(f"{source_id}:{content_hash}" for source_id, content_hash in documents)
    if not lines:
        return None
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()[:VERSION_LENGTH]


async def index_summary(sessionmaker: async_sessionmaker[AsyncSession]) -> IndexSummary:
    """Версия индекса, число действующих документов и их фрагментов."""
    async with sessionmaker() as session:
        rows = (
            await session.execute(
                select(
                    models.Document.source_id,
                    models.Document.content_hash,
                    func.count(models.Fragment.id),
                )
                .outerjoin(models.Fragment, models.Fragment.document_id == models.Document.id)
                .where(models.Document.status == "active")
                .group_by(models.Document.id)
            )
        ).all()
    return IndexSummary(
        version=index_version((source_id, content_hash) for source_id, content_hash, _ in rows),
        documents=len(rows),
        fragments=sum(count for _, _, count in rows),
    )


async def export_index(sessionmaker: async_sessionmaker[AsyncSession]) -> IndexDump:
    """Действующие документы с фрагментами и векторами.

    Документы и фрагменты читаются одним запросом: выгрузка видит один снимок базы.

    Raises:
        TransferError: В базе нет действующих документов.
    """
    async with sessionmaker() as session:
        rows = (
            await session.execute(
                select(models.Document, models.Fragment)
                .outerjoin(models.Fragment, models.Fragment.document_id == models.Document.id)
                .options(undefer(models.Fragment.embedding))
                .where(models.Document.status == "active")
                .order_by(models.Document.source_id, models.Fragment.position)
            )
        ).all()
    documents: dict[UUID, models.Document] = {}
    fragments: dict[UUID, list[FragmentDump]] = {}
    for document, fragment in rows:
        documents.setdefault(document.id, document)
        found = fragments.setdefault(document.id, [])
        if fragment is not None:
            found.append(FragmentDump.model_validate(fragment))
    if not documents:
        raise TransferError("в базе нет действующих документов")
    dumps = [_document_dump(document, fragments[id_]) for id_, document in documents.items()]
    return IndexDump(
        format=FORMAT,
        knowledge_schema=KNOWLEDGE_SCHEMA_REVISION,
        embedding_model=MODEL_ALIAS,
        dimensions=models.EMBEDDING_DIMENSIONS,
        version=index_version((d.source_id, d.content_hash) for d in dumps),
        documents=dumps,
    )


def _document_dump(document: models.Document, fragments: list[FragmentDump]) -> DocumentDump:
    return DocumentDump.model_validate(
        {
            "id": document.id,
            "source_id": document.source_id,
            "version": document.version,
            "title": document.title,
            "url": document.url,
            "kind": document.kind,
            "content_hash": document.content_hash,
            "created_at": document.created_at,
            "fragments": fragments,
        }
    )


async def import_index(
    sessionmaker: async_sessionmaker[AsyncSession], data: bytes
) -> list[ImportReport]:
    """Сливает выгрузку с индексом базы одной транзакцией (спека #109, 4.2).

    По каждому документу файла: тот же `id` уже действует — без изменений; `id` есть среди
    прежних версий — он снова действует; `id` нет — документ и фрагменты вставляются.
    В двух последних случаях действовавший документ источника становится прежней версией.
    Источники, которых нет в файле, не меняются.

    Raises:
        TransferError: Файл не по схеме или без документов; другой формат, ревизия схемы
            `knowledge`, модель или размерность; `id` документа в базе принадлежит другому
            источнику или хешу.
    """
    _check_compatible(_validate(DumpHeader, data))
    dump = _validate(IndexDump, data)
    if not dump.documents:
        raise TransferError("в файле нет документов")
    async with sessionmaker() as session, session.begin():
        return [await _merge(session, document) for document in dump.documents]


def _validate[Model: BaseModel](model: type[Model], data: bytes) -> Model:
    try:
        return model.model_validate_json(data)
    except ValidationError as exc:
        # Значения не выводим: в них текст документов и векторы.
        errors = exc.errors(include_input=False, include_url=False)
        first = errors[0]
        where = ".".join(str(part) for part in first["loc"]) or "файл"
        raise TransferError(
            f"файл не по схеме выгрузки: {where}: {first['msg']} (ошибок: {len(errors)})"
        ) from None


def _check_compatible(header: DumpHeader) -> None:
    checks = (
        ("формат файла", header.format, FORMAT),
        ("ревизия схемы knowledge", header.knowledge_schema, KNOWLEDGE_SCHEMA_REVISION),
        ("модель эмбеддингов", header.embedding_model, MODEL_ALIAS),
        ("размерность векторов", header.dimensions, models.EMBEDDING_DIMENSIONS),
    )
    for name, in_file, in_code in checks:
        if in_file != in_code:
            raise TransferError(f"{name}: в файле {in_file}, в коде {in_code}")


async def _merge(session: AsyncSession, document: DocumentDump) -> ImportReport:
    existing = await session.get(models.Document, document.id)
    if existing is None:
        await _supersede_active(session, document.source_id)
        await _insert(session, document)
        return ImportReport(document.source_id, "loaded", fragments=len(document.fragments))
    if (existing.source_id, existing.content_hash) != (document.source_id, document.content_hash):
        raise TransferError(
            f"{document.source_id}: документ {document.id} уже есть в базе с другими данными:"
            f" источник {existing.source_id}, хеш {existing.content_hash}"
        )
    if existing.status == "active":
        return ImportReport(document.source_id, "unchanged")
    await _supersede_active(session, document.source_id)
    existing.status = "active"
    existing.superseded_at = None
    await session.flush()
    return ImportReport(document.source_id, "restored")


async def _supersede_active(session: AsyncSession, source_id: str) -> None:
    """Действующий документ источника становится прежней версией, как при `make index`."""
    await session.execute(
        update(models.Document)
        .where(models.Document.source_id == source_id, models.Document.status == "active")
        .values(status="superseded", superseded_at=func.now())
        .execution_options(synchronize_session=False)
    )


async def _insert(session: AsyncSession, document: DocumentDump) -> None:
    session.add(
        models.Document(
            id=document.id,
            source_id=document.source_id,
            version=document.version,
            title=document.title,
            url=document.url,
            kind=document.kind,
            content_hash=document.content_hash,
            status="active",
            created_at=document.created_at,
        )
    )
    await session.flush()
    if document.fragments:
        await session.execute(
            insert(models.Fragment),
            [{**f.model_dump(), "document_id": document.id} for f in document.fragments],
        )


__all__ = [
    "FORMAT",
    "KNOWLEDGE_SCHEMA_REVISION",
    "DocumentDump",
    "DumpHeader",
    "FragmentDump",
    "ImportReport",
    "IndexDump",
    "TransferError",
    "export_index",
    "import_index",
    "index_summary",
    "index_version",
]
