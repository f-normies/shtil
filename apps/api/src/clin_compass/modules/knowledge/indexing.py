"""Индексация источников манифеста: `make index` (спека #27, раздел 5).

Работу запускает человек раз в несколько дней и ждёт вывода, поэтому команда выполняет её
сама, без очереди. По каждому источнику: исходник → хеш → разбор → эмбеддинги → одна
транзакция. Ошибка одного источника не останавливает остальные.
"""

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

import clin_compass_knowledge
import httpx2
import structlog
from clin_compass_knowledge.schemas.source import Source
from pydantic_ai import Embedder
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.core.ai.docparse import ParsedPdf, build_docparse_client
from clin_compass.core.ai.embeddings import build_embedder
from clin_compass.core.ai.nodes import AiError, AiNotConfiguredError
from clin_compass.core.ai.runs import DbRunRecorder, describe_error
from clin_compass.core.ai.textlayer import PageMismatch, reconcile_pages
from clin_compass.core.config import Settings
from clin_compass.modules.knowledge import models
from clin_compass.modules.knowledge.fragments import ParsedFragment, ParseError
from clin_compass.modules.knowledge.guidelines import parse_guideline
from clin_compass.modules.knowledge.label import parse_label

log = structlog.get_logger()

INDEX_TASK = "knowledge.index"
PARSE_TASK = "knowledge.parse"
# Эмбеддинги считаются пачками: вход llama-server ограничен `-ub`, фрагмент — до 20 000 знаков.
BATCH = 8
# Ответ API рубрикатора — несколько мегабайт JSON.
GUIDELINE_TIMEOUT_SECONDS = 60.0

Status = Literal["indexed", "unchanged", "error"]


class SourceError(Exception):
    """Источник не проиндексирован; текст ошибки — для вывода команды, без текста документа."""


class GuidelineSource(Protocol):
    """Откуда берётся JSON клинической рекомендации по версии из манифеста."""

    async def fetch(self, version: str) -> bytes:
        """Ответ `GetClinrec2` целиком."""
        ...


class PdfParser(Protocol):
    """Разбор PDF по страницам: `DocparseClient`."""

    async def parse_pdf(self, pdf: bytes, *, task: str) -> ParsedPdf:
        """Страницы PDF с текстом модели и текстовым слоем."""
        ...


class HttpGuidelines:
    """JSON API рубрикатора клинических рекомендаций Минздрава."""

    def __init__(self, url: str, *, timeout: float = GUIDELINE_TIMEOUT_SECONDS) -> None:
        self._url = url
        self._timeout = timeout

    async def fetch(self, version: str) -> bytes:
        """`GET {url}?op=GetClinrec2&id={version}`.

        Raises:
            httpx2.HTTPError: Сеть или ответ не 2xx.
        """
        async with httpx2.AsyncClient(timeout=self._timeout) as client:
            response = await client.get(self._url, params={"op": "GetClinrec2", "id": version})
            response.raise_for_status()
            return response.content


@dataclass(frozen=True)
class SourceReport:
    """Итог по источнику: строка вывода команды."""

    source_id: str
    status: Status
    fragments: int = 0
    detail: str = ""
    # Что ещё нужно знать человеку: например, страницы без текстового слоя.
    notes: tuple[str, ...] = ()

    def line(self) -> str:
        """`<id>: проиндексирован, фрагментов: N`, `<id>: без изменений` или `<id>: ошибка: …`."""
        if self.status == "indexed":
            line = f"{self.source_id}: проиндексирован, фрагментов: {self.fragments}"
            return "; ".join((line, *self.notes))
        if self.status == "unchanged":
            return f"{self.source_id}: без изменений"
        return f"{self.source_id}: ошибка: {self.detail}"


class Indexer:
    """Индексирует источники `clinical_guideline` и `drug_label` манифеста."""

    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        embedder: Embedder,
        *,
        guidelines: GuidelineSource,
        docparse: PdfParser | None,
        root: Path,
    ) -> None:
        """
        Args:
            sessionmaker: Фабрика сессий БД.
            embedder: Клиент эмбеддингов с задачей `knowledge.index`.
            guidelines: API рубрикатора.
            docparse: Разбор PDF; None — узлы docparse не заданы, инструкции не индексируются.
            root: Каталог knowledge/: файлы источников лежат в `sources/`.
        """
        self._sessionmaker = sessionmaker
        self._embedder = embedder
        self._guidelines = guidelines
        self._docparse = docparse
        self._root = root

    async def index(self, sources: Sequence[Source], *, force: bool = False) -> list[SourceReport]:
        """Итог по каждому индексируемому источнику в порядке манифеста.

        `force` переиндексирует документ и при том же хеше: это нужно после правки разбора.
        """
        reports = []
        for source in sources:
            if source.kind not in ("clinical_guideline", "drug_label"):
                continue
            reports.append(await self._index_one(source, force=force))
        return reports

    async def _index_one(self, source: Source, *, force: bool) -> SourceReport:
        try:
            raw = await self._read(source)
            content_hash = hashlib.sha256(raw).hexdigest()
            if not force and await self._is_current(source.id, content_hash):
                return SourceReport(source.id, "unchanged")
            fragments, notes = await self._parse(source, raw)
            embeddings = await self._embed(source, fragments)
            await self._store(source, content_hash, fragments, embeddings)
        except (SourceError, ParseError, AiError) as exc:
            return SourceReport(source.id, "error", detail=str(exc))
        except httpx2.HTTPError as exc:
            return SourceReport(
                source.id, "error", detail=f"API рубрикатора: {describe_error(exc)}"
            )
        # Любая другая ошибка — тоже итог источника: остальные источники индексируются.
        except Exception as exc:
            log.exception("knowledge.index_failed", source_id=source.id)
            return SourceReport(source.id, "error", detail=describe_error(exc))
        return SourceReport(source.id, "indexed", fragments=len(fragments), notes=notes)

    async def _read(self, source: Source) -> bytes:
        """Исходник: JSON рекомендации из API или PDF инструкции из `sources/files/`."""
        if source.kind == "clinical_guideline":
            if source.file is not None:
                raise SourceError("клиническая рекомендация берётся из API рубрикатора: file: null")
            return await self._guidelines.fetch(source.version)
        if source.file is None:
            raise SourceError("у инструкции нет файла PDF в манифесте (file)")
        return (self._root / "sources" / source.file).read_bytes()

    async def _is_current(self, source_id: str, content_hash: str) -> bool:
        async with self._sessionmaker() as session:
            found = await session.scalar(
                select(models.Document.id).where(
                    models.Document.source_id == source_id,
                    models.Document.status == "active",
                    models.Document.content_hash == content_hash,
                )
            )
        return found is not None

    async def _parse(
        self, source: Source, raw: bytes
    ) -> tuple[list[ParsedFragment], tuple[str, ...]]:
        """Фрагменты и заметки для вывода. Слова инструкции берутся из текстового слоя PDF.

        Документ без фрагментов или рекомендация без пунктов — ошибка: рабочую версию
        нельзя заменять пустой.
        """
        if source.kind == "clinical_guideline":
            fragments = _not_empty(parse_guideline(_guideline_json(raw, source.version)))
            if not any(f.kind == "recommendation" for f in fragments):
                raise ParseError("в рекомендации нет ни одного пункта со строкой уровней")
            return fragments, ()
        if self._docparse is None:
            raise SourceError("не заданы адреса узлов docparse (COMPASS_DOCPARSE_URLS)")
        parsed = await self._docparse.parse_pdf(raw, task=PARSE_TASK)
        pages, mismatches = reconcile_pages(parsed.pages)
        if mismatches:
            raise SourceError(
                "текст модели не сведён с текстовым слоем PDF: "
                + "; ".join(_mismatch(m) for m in mismatches)
            )
        notes = tuple(
            f"страница {page.number}: текст из слоя PDF без разметки модели"
            for page in pages
            if page.layer_only
        )
        return _not_empty(parse_label([page.text for page in pages])), notes

    async def _embed(
        self, source: Source, fragments: Sequence[ParsedFragment]
    ) -> list[list[float]]:
        """Эмбеддинги документов по строке «название. путь заголовков\\nтекст» пачками."""
        texts = [f"{source.title}. {f.heading_path}\n{f.text}" for f in fragments]
        embeddings: list[list[float]] = []
        for start in range(0, len(texts), BATCH):
            result = await self._embedder.embed_documents(texts[start : start + BATCH])
            embeddings.extend(list(vector) for vector in result.embeddings)
        return embeddings

    async def _store(
        self,
        source: Source,
        content_hash: str,
        fragments: Sequence[ParsedFragment],
        embeddings: Sequence[list[float]],
    ) -> None:
        """Одна транзакция: прежний `active` → `superseded`, новый документ с фрагментами."""
        async with self._sessionmaker() as session:
            await session.execute(
                update(models.Document)
                .where(models.Document.source_id == source.id, models.Document.status == "active")
                .values(status="superseded", superseded_at=func.now())
            )
            document = models.Document(
                source_id=source.id,
                version=source.version,
                title=source.title,
                url=str(source.url),
                kind=source.kind,
                content_hash=content_hash,
                status="active",
            )
            session.add(document)
            await session.flush()
            numbers: dict[str, int] = {}
            for position, (fragment, embedding) in enumerate(
                zip(fragments, embeddings, strict=True), start=1
            ):
                numbers[fragment.section] = numbers.get(fragment.section, 0) + 1
                session.add(
                    models.Fragment(
                        document_id=document.id,
                        position=position,
                        key=f"{source.id}/{fragment.section}/{numbers[fragment.section]}",
                        section=fragment.section,
                        section_title=fragment.section_title,
                        heading_path=fragment.heading_path,
                        kind=fragment.kind,
                        text=fragment.text,
                        uur=fragment.uur,
                        udd=fragment.udd,
                        embedding=embedding,
                    )
                )
            await session.commit()


def _guideline_json(raw: bytes, version: str) -> dict[str, Any]:
    """JSON ответа API; его `id` должен совпасть с версией в манифесте."""
    try:
        data = json.loads(raw)
    except ValueError:
        raise SourceError("ответ API рубрикатора — не JSON") from None
    if not isinstance(data, dict):
        raise SourceError("ответ API рубрикатора — не объект JSON")
    if str(data.get("id")) != version:
        raise SourceError(
            f"API рубрикатора вернул документ {data.get('id')!r}, в манифесте версия {version!r}"
        )
    return data


def _not_empty(fragments: list[ParsedFragment]) -> list[ParsedFragment]:
    if not fragments:
        raise ParseError("документ не дал ни одного фрагмента")
    return fragments


def _mismatch(mismatch: PageMismatch) -> str:
    """Страница, доля несведённых токенов и примеры расхождений «модель → слой»."""
    if mismatch.no_layer:
        return f"страница {mismatch.number}: нет текстового слоя"
    examples = ", ".join(f"«{example}»" for example in mismatch.examples)
    return (
        f"страница {mismatch.number}: не сведено {mismatch.unmatched:.0%} токенов, "
        f"например {examples}"
    )


def build_indexer(
    settings: Settings, sessionmaker: async_sessionmaker[AsyncSession], root: Path
) -> Indexer:
    """Индексатор с клиентами из настроек; вызовы моделей пишутся в ai_runs.

    Без узлов docparse инструкции не индексируются: это ошибка в строке источника.

    Raises:
        AiNotConfiguredError: Не заданы адреса узлов эмбеддингов.
    """
    recorder = DbRunRecorder(sessionmaker)
    embedder = build_embedder(settings, recorder, task=INDEX_TASK)
    try:
        docparse: PdfParser | None = build_docparse_client(settings, recorder)
    except AiNotConfiguredError:
        docparse = None
    return Indexer(
        sessionmaker,
        embedder,
        guidelines=HttpGuidelines(settings.knowledge_guidelines_api_url),
        docparse=docparse,
        root=root,
    )


def knowledge_root(settings: Settings) -> Path:
    """Каталог knowledge/: из настроек или этой рабочей копии (пакет лежит в knowledge/src/…)."""
    return settings.knowledge_dir or Path(clin_compass_knowledge.__file__).resolve().parents[2]
