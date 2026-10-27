"""Задачи worker'а: распознавание и извлечение истории болезни (спека #30, 3.3–3.7).

Вызовы docparse и LLM идут вне транзакции. Результат этапа пишется одной транзакцией под
блокировкой документа: если worker упал после коммита, повтор задачи увидит другой статус
и выйдет.

Повторы задача делает сама через `RetryRequested`, как в `freetext`: пока узел или S3
недоступен, она ждёт без счёта попыток. Нечитаемая страница, пустой и слишком длинный текст
сразу дают `failed` с кодом. Другие ошибки считаются в документе; пятая даёт `failed`.
"""

from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from datetime import timedelta
from pathlib import Path
from typing import Literal, Protocol
from uuid import UUID

import anyio
import clin_compass_knowledge
import structlog
from botocore.exceptions import BotoCoreError
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.core import queue
from clin_compass.core.ai.docparse import DocparsePageError, ParsedPdf, build_docparse_client
from clin_compass.core.ai.llm import Llm, build_llm_model
from clin_compass.core.ai.nodes import NodesUnavailableError
from clin_compass.core.ai.runs import DbRunRecorder, describe_error
from clin_compass.core.config import Settings
from clin_compass.core.queue import Entrypoint, RetryRequested
from clin_compass.core.storage import (
    S3Storage,
    StorageError,
    StorageObjectMissingError,
    StorageUnavailableError,
    build_storage,
)
from clin_compass.modules.ingestion import files
from clin_compass.modules.ingestion.analysis import Extractor
from clin_compass.modules.ingestion.document import PageSpan, document_text, text_problem
from clin_compass.modules.ingestion.models import Document
from clin_compass.modules.ingestion.payloads import card_split
from clin_compass.modules.ingestion.schemas import ErrorCode
from clin_compass.modules.ingestion.service import (
    EXTRACT_ENTRYPOINT,
    MAX_DOCUMENT_BYTES,
    PARSE_ENTRYPOINT,
)
from clin_compass.modules.proposals import service as proposals
from clin_compass.modules.therapy.deps import Catalog

log = structlog.get_logger()

Stage = Literal["parse", "extract"]
FAILURE_LIMIT = 5
# Пока Мак выключен или S3 недоступен, задача проверяет его раз в 30 с.
UNAVAILABLE_DELAY = timedelta(seconds=30)
_FIRST_RETRY_SECONDS = 2
# Сервер docparse принимает один запрос за раз (-np 1); LLM держит два запроса, их занимает
# freetext. Лимит общий для всех процессов worker'а.
_CONCURRENCY = 1
_STAGE_STATUS: dict[Stage, str] = {"parse": "parsing", "extract": "extracting"}
_STAGE_FAILED: dict[Stage, ErrorCode] = {"parse": "parse_failed", "extract": "extract_failed"}


class PdfParser(Protocol):
    """Разбор PDF по страницам: `DocparseClient`."""

    async def parse_pdf(self, pdf: bytes, *, task: str) -> ParsedPdf:
        """Страницы PDF с текстом модели и текстовым слоем."""
        ...


class DocumentTextError(Exception):
    """Текст документа не годится для извлечения: повтор не поможет."""

    def __init__(self, code: ErrorCode) -> None:
        super().__init__(code)
        self.code: ErrorCode = code


class _DependencyUnavailableError(Exception):
    """S3 не настроен: задача ждёт, как при недоступном узле."""


@dataclass(frozen=True)
class Jobs:
    """Обработчики двух задач. Без клиента этапа его задача не регистрируется."""

    sessionmaker: async_sessionmaker[AsyncSession]
    storage: S3Storage | None
    docparse: PdfParser | None
    extractor: Extractor | None

    def handlers(self) -> dict[str, Entrypoint]:
        """Точки входа для `worker.build_handlers`."""
        handlers: dict[str, Entrypoint] = {}
        if self.docparse is not None:
            handlers[PARSE_ENTRYPOINT] = Entrypoint(self.parse, concurrency_limit=_CONCURRENCY)
        if self.extractor is not None:
            handlers[EXTRACT_ENTRYPOINT] = Entrypoint(self.extract, concurrency_limit=_CONCURRENCY)
        return handlers

    async def parse(self, payload: bytes | None) -> None:
        """Распознать документ и поставить извлечение."""
        await self._run(_document_id(payload), "parse", self._parse)

    async def extract(self, payload: bytes | None) -> None:
        """Извлечь факты: каждый — предложение врачу; уже известные — в `known`."""
        await self._run(_document_id(payload), "extract", self._extract)

    async def _parse(self, document_id: UUID) -> None:
        document = await self._pending(document_id, "parse")
        if document is None:
            return
        if self.storage is None or self.docparse is None:
            raise _DependencyUnavailableError("storage")
        data = await anyio.to_thread.run_sync(
            _read_object, self.storage, document.object_key, document.size
        )
        pdf = await anyio.to_thread.run_sync(files.to_pdf, data, document.content_type)
        parsed = await self.docparse.parse_pdf(pdf, task=PARSE_ENTRYPOINT)
        # Сведение со слоем — сотни миллисекунд на страницу: вне цикла событий.
        built = await anyio.to_thread.run_sync(document_text, parsed.pages)
        problem = text_problem(built.text)
        if problem is not None:
            raise DocumentTextError(problem)
        async with self.sessionmaker() as session:
            locked = await _lock(session, document_id)
            if locked.status != "parsing":
                return
            locked.text = built.text
            locked.page_sources = [asdict(p) for p in built.pages]
            locked.ocr = built.ocr
            locked.parsed_at = func.now()
            locked.status = "extracting"
            payload = str(document_id).encode()
            await queue.enqueue(
                session,
                EXTRACT_ENTRYPOINT,
                payload,
                dedupe_key=f"{EXTRACT_ENTRYPOINT}:{document_id}",
            )
            await session.commit()

    async def _extract(self, document_id: UUID) -> None:
        document = await self._pending(document_id, "extract")
        if document is None or self.extractor is None:
            return
        text = document.text or ""
        result = await self.extractor.extract(text, ocr=bool(document.ocr))
        spans = [PageSpan(**p) for p in document.page_sources]
        async with self.sessionmaker() as session:
            locked = await _lock(session, document_id)
            if locked.status != "extracting":
                return
            offers, known = await card_split(session, locked, result, text, spans)
            for kind, payload in offers:
                await proposals.create_proposal(
                    session,
                    patient_id=locked.patient_id,
                    kind=kind,
                    payload=payload,
                    origin_type="ai_run",
                    origin_ref=str(result.run_id),
                )
            locked.known = known
            locked.document_date = result.document_date
            locked.dropped = result.dropped
            locked.extract_run_id = result.run_id
            locked.extracted_at = func.now()
            locked.status = "done"
            await session.commit()

    async def _pending(self, document_id: UUID, stage: Stage) -> Document | None:
        """Документ, если он ждёт этот этап."""
        async with self.sessionmaker() as session:
            document = await session.get(Document, document_id)
        if document is None:
            log.warning("ingestion.document_missing", document_id=str(document_id), stage=stage)
            return None
        return document if document.status == _STAGE_STATUS[stage] else None

    async def _run(
        self, document_id: UUID, stage: Stage, work: Callable[[UUID], Awaitable[None]]
    ) -> None:
        """Этап целиком; наружу выходит только `RetryRequested`.

        Повтор поднимается без цепочки причин (`from None`): PgQueuer пишет трассировку
        в журнал pgq, а в ошибке базы бывают параметры запроса — текст документа.
        """
        try:
            await work(document_id)
        except (NodesUnavailableError, StorageError, _DependencyUnavailableError) as exc:
            reason = describe_error(exc)
            log.warning(
                "ingestion.waiting", document_id=str(document_id), stage=stage, error=reason
            )
            raise RetryRequested(delay=UNAVAILABLE_DELAY, reason=reason) from None
        except DocparsePageError as exc:
            await self._settle(
                document_id, stage, self._fail(document_id, stage, "page_unreadable", exc.page)
            )
        except DocumentTextError as exc:
            await self._settle(document_id, stage, self._fail(document_id, stage, exc.code))
        # Любая другая ошибка этапа — повтор или отметка о неудаче: обычная ошибка из
        # обработчика могла бы стать последней попыткой PgQueuer без нашей отметки.
        except Exception as exc:  # noqa: BLE001
            await self._count(document_id, stage, describe_error(exc))

    async def _count(self, document_id: UUID, stage: Stage, reason: str) -> None:
        """Неудача этапа: повтор с растущей паузой, пятая — `failed`."""
        failures = await self._settle(document_id, stage, self._record_failure(document_id, stage))
        # Текст ошибки не пишем: в нём может повториться текст документа.
        log.warning(
            "ingestion.stage_error",
            document_id=str(document_id),
            stage=stage,
            error=reason,
            failures=failures,
        )
        if failures is not None and failures < FAILURE_LIMIT:
            delay = timedelta(seconds=_FIRST_RETRY_SECONDS * 2 ** (failures - 1))
            raise RetryRequested(delay=delay, reason=reason) from None

    async def _settle[T](self, document_id: UUID, stage: Stage, write: Awaitable[T]) -> T:
        """Запись итога в своей транзакции. База недоступна — ждём её, как узел модели."""
        try:
            return await write
        except Exception as exc:  # noqa: BLE001 — см. `_run`
            log.warning(
                "ingestion.failure_not_recorded",
                document_id=str(document_id),
                stage=stage,
                error=describe_error(exc),
            )
            raise RetryRequested(delay=UNAVAILABLE_DELAY, reason="database") from None

    async def _record_failure(self, document_id: UUID, stage: Stage) -> int | None:
        """Счётчик неудач этапа +1; на `FAILURE_LIMIT` — `failed`. None — этап уже не ждёт."""
        failures = Document.parse_failures if stage == "parse" else Document.extract_failures
        async with self.sessionmaker() as session:
            count: int | None = await session.scalar(
                update(Document)
                .where(Document.id == document_id, Document.status == _STAGE_STATUS[stage])
                .values({failures: failures + 1})
                .returning(failures)
            )
            if count is not None and count >= FAILURE_LIMIT:
                await session.execute(
                    update(Document)
                    .where(Document.id == document_id)
                    .values(status="failed", error_code=_STAGE_FAILED[stage])
                )
                log.error("ingestion.stage_failed", document_id=str(document_id), stage=stage)
            await session.commit()
        return count

    async def _fail(
        self, document_id: UUID, stage: Stage, code: ErrorCode, page: int | None = None
    ) -> None:
        """Сразу `failed` с кодом: повтор не поможет, врач видит причину."""
        async with self.sessionmaker() as session:
            await session.execute(
                update(Document)
                .where(Document.id == document_id, Document.status == _STAGE_STATUS[stage])
                .values(status="failed", error_code=code, error_page=page)
            )
            await session.commit()
        log.warning("ingestion.stage_rejected", document_id=str(document_id), code=code)


def build_jobs(settings: Settings, sessionmaker: async_sessionmaker[AsyncSession]) -> Jobs:
    """Задачи с клиентами из настроек: docparse — по `COMPASS_DOCPARSE_URLS`, LLM —
    по `COMPASS_LLM_URLS`. Вызовы моделей пишутся в ai_runs.

    Raises:
        KnowledgeError: Данные knowledge/ не прошли проверку.
        ValueError: В knowledge/ нет снимка ГРЛС.
    """
    recorder = DbRunRecorder(sessionmaker)
    docparse = build_docparse_client(settings, recorder) if settings.docparse_urls else None
    extractor = None
    if settings.llm_urls:
        root = settings.knowledge_dir or _repository_knowledge_dir()
        registry = Catalog.load(root).registry
        if registry is None:
            raise ValueError("в knowledge/ нет снимка ГРЛС (источник kind: drug_registry)")
        extractor = Extractor(Llm(build_llm_model(settings), recorder), registry)
    return Jobs(sessionmaker, build_storage(settings), docparse, extractor)


def _repository_knowledge_dir() -> Path:
    """knowledge/ рабочей копии, как в `freetext.tasks`: пакет лежит в knowledge/src/…"""
    return Path(clin_compass_knowledge.__file__).resolve().parents[2]


def _read_object(storage: S3Storage, key: str, size: int) -> bytes:
    """Оригинал из S3. Пропавший объект — обычная ошибка этапа: оригиналы не удаляются."""
    try:
        stored = storage.get(key)
    except StorageObjectMissingError:
        raise RuntimeError("document original is missing") from None
    try:
        data = stored.body.read(MAX_DOCUMENT_BYTES + 1)
    except (BotoCoreError, OSError):
        # Поток S3 оборвался после GetObject: это недоступность хранилища, как в messaging.
        raise StorageUnavailableError("S3 read failed") from None
    finally:
        stored.body.close()
    if len(data) != size:
        raise RuntimeError("document original has another size")
    return data


async def _lock(session: AsyncSession, document_id: UUID) -> Document:
    document = await session.scalar(
        select(Document)
        .where(Document.id == document_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if document is None:
        raise RuntimeError(f"document {document_id} disappeared")
    return document


def _document_id(payload: bytes | None) -> UUID:
    if payload is None:
        raise ValueError("ingestion job without document id")
    return UUID(payload.decode())
