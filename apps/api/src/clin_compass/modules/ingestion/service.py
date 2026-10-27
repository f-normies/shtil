"""Публичный интерфейс модуля ingestion (спека #30, раздел 3).

Врач загружает историю болезни: оригинал ложится в S3, документ — в базу, распознавание
и извлечение идут в worker'е (`tasks.py`). Факты приходят врачу предложениями; подтверждённые
анамнез, факторы риска и прошлые препараты без даты отмены читают другие модули
через `list_confirmed`.
"""

import hashlib
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID, uuid4

import anyio
import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.core import queue
from clin_compass.core.storage import S3Storage, StorageError
from clin_compass.modules.ingestion import appliers, files
from clin_compass.modules.ingestion.models import Document, Fact
from clin_compass.modules.ingestion.schemas import DocumentOut, FactField, FactOut
from clin_compass.modules.proposals.service import ApplierRegistry
from clin_compass.modules.therapy.deps import Catalog

log = structlog.get_logger()

PARSE_ENTRYPOINT = "ingestion.parse"
EXTRACT_ENTRYPOINT = "ingestion.extract"
MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
_FILENAME_LENGTH = 200


class DocumentNotFoundError(Exception):
    """У пациента нет такого документа."""


class DocumentNotFailedError(Exception):
    """Повторить можно только документ со статусом `failed`."""


class DocumentCommitUnknownError(Exception):
    """Ответ COMMIT потерян, а проверка новым соединением не доказала его исход."""


@dataclass(frozen=True)
class StoredFile:
    """Где лежит оригинал и с каким типом его отдавать."""

    object_key: str
    content_type: str
    filename: str


async def accept_document(
    session: AsyncSession,
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: S3Storage,
    *,
    patient_id: UUID,
    doctor_id: UUID,
    filename: str,
    data: bytes,
) -> tuple[DocumentOut, bool]:
    """Принять файл: S3, строка документа и задача распознавания. Коммитит сам.

    Тот же файл у того же пациента возвращает уже созданный документ (`False`).

    Raises:
        files.DocumentFileError: Файл не принят: тип, страницы, не открывается.
        StorageError: S3 не принял объект.
        DocumentCommitUnknownError: Неизвестно, записан ли документ.
    """
    checked = await anyio.to_thread.run_sync(files.inspect, data)
    digest = hashlib.sha256(data).hexdigest()
    existing = await _by_hash(session, patient_id, digest)
    # Сетевая операция — вне транзакции.
    await session.rollback()
    if existing is not None:
        return existing, False
    object_key = f"documents/{uuid4()}"
    await anyio.to_thread.run_sync(storage.put, object_key, data, checked.content_type)
    result: DocumentOut | None = None
    try:
        created: UUID | None = await session.scalar(
            insert(Document)
            .values(
                patient_id=patient_id,
                uploaded_by=doctor_id,
                filename=_filename(filename),
                content_type=checked.content_type,
                size=len(data),
                sha256=digest,
                object_key=object_key,
                pages=checked.pages,
            )
            .on_conflict_do_nothing(index_elements=["patient_id", "sha256"])
            .returning(Document.id)
        )
        if created is not None:
            await queue.enqueue(
                session,
                PARSE_ENTRYPOINT,
                str(created).encode(),
                dedupe_key=f"{PARSE_ENTRYPOINT}:{created}",
            )
            result = DocumentOut.model_validate(await session.get_one(Document, created))
    except BaseException:
        # До COMMIT документа никто не видит: объект этой попытки удаляем при любой ошибке.
        with anyio.CancelScope(shield=True):
            try:
                await session.rollback()
            finally:
                await _delete_attempt(storage, object_key)
        raise
    if result is None:
        # Тот же файл загрузили одновременно, и другой запрос успел раньше.
        await session.rollback()
        await _delete_attempt(storage, object_key)
        winner = await _by_hash(session, patient_id, digest)
        await session.rollback()
        if winner is None:
            raise RuntimeError("document conflict without an existing row")
        return winner, False
    try:
        await session.commit()
    except SQLAlchemyError:
        await session.rollback()
        return await _resolve_commit(sessionmaker, storage, object_key), True
    return result, True


async def _resolve_commit(
    sessionmaker: async_sessionmaker[AsyncSession], storage: S3Storage, object_key: str
) -> DocumentOut:
    """COMMIT упал: документ есть — он и создан, нет — объект удаляется, ошибка наверх."""
    try:
        async with sessionmaker() as check:
            row = await check.scalar(select(Document).where(Document.object_key == object_key))
            found = DocumentOut.model_validate(row) if row is not None else None
    except SQLAlchemyError:
        raise DocumentCommitUnknownError("document commit outcome is unknown") from None
    if found is None:
        await _delete_attempt(storage, object_key)
        raise DocumentCommitUnknownError("document was not committed")
    return found


async def list_documents(session: AsyncSession, patient_id: UUID) -> list[DocumentOut]:
    """Документы пациента, новые первыми. Доступ проверяет вызывающий."""
    rows = await session.scalars(
        select(Document)
        .where(Document.patient_id == patient_id)
        .order_by(Document.created_at.desc(), Document.id.desc())
    )
    return [DocumentOut.model_validate(d) for d in rows]


async def stored_file(session: AsyncSession, patient_id: UUID, document_id: UUID) -> StoredFile:
    """Ключ S3 и тип оригинала документа этого пациента.

    Raises:
        DocumentNotFoundError: У пациента нет такого документа.
    """
    row = await session.get(Document, document_id)
    if row is None or row.patient_id != patient_id:
        raise DocumentNotFoundError
    return StoredFile(row.object_key, row.content_type, row.filename)


async def retry(session: AsyncSession, patient_id: UUID, document_id: UUID) -> DocumentOut:
    """Повторить этап, на котором документ остановился. Коммитит вызывающий.

    Счётчик неудач этапа и ошибка сбрасываются, задача этапа ставится заново.

    Raises:
        DocumentNotFoundError: У пациента нет такого документа.
        DocumentNotFailedError: Документ не в статусе `failed`.
    """
    row = await session.scalar(
        select(Document)
        .where(Document.id == document_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None or row.patient_id != patient_id:
        raise DocumentNotFoundError
    if row.status != "failed":
        raise DocumentNotFailedError
    row.error_code = None
    row.error_page = None
    if row.parsed_at is None:
        row.status, row.parse_failures, entrypoint = "parsing", 0, PARSE_ENTRYPOINT
    else:
        row.status, row.extract_failures, entrypoint = "extracting", 0, EXTRACT_ENTRYPOINT
    await session.flush()
    # Без ключа повтора: прежняя задача этапа уже завершилась, а два повтора подряд
    # не пройдут проверку статуса под блокировкой строки.
    await queue.enqueue(session, entrypoint, str(row.id).encode())
    return DocumentOut.model_validate(row)


async def list_confirmed(
    session: AsyncSession, *, patient_id: UUID, fields: Sequence[FactField] | None = None
) -> list[FactOut]:
    """Подтверждённые факты пациента по времени подтверждения.

    Право доступа проверяет вызывающий (`patients.deps.owned_patient`).
    """
    query = select(Fact).where(Fact.patient_id == patient_id)
    if fields is not None:
        query = query.where(Fact.field.in_(list(fields)))
    rows = await session.scalars(query.order_by(Fact.confirmed_at, Fact.id))
    return [FactOut.model_validate(f) for f in rows]


def register_appliers(registry: ApplierRegistry, catalog: Catalog) -> None:
    """Appliers трёх видов предложений; `catalog` — справочник препаратов `therapy`."""
    appliers.register(registry, catalog)


async def _by_hash(session: AsyncSession, patient_id: UUID, digest: str) -> DocumentOut | None:
    row = await session.scalar(
        select(Document).where(Document.patient_id == patient_id, Document.sha256 == digest)
    )
    return DocumentOut.model_validate(row) if row is not None else None


async def _delete_attempt(storage: S3Storage, key: str) -> None:
    """Удалить объект попытки, которая не создала документ. Ошибка S3 — только в лог."""
    try:
        await anyio.to_thread.run_sync(storage.delete, key)
    except StorageError as error:
        log.warning("ingestion.orphan_cleanup_failed", error_type=type(error).__name__)


def _filename(raw: str) -> str:
    """Имя файла без пути и управляющих символов, до 200 знаков."""
    name = raw.replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(ch for ch in name if unicodedata.category(ch)[0] != "C").strip()
    return name[:_FILENAME_LENGTH] or "document"
