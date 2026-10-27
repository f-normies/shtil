"""Помощники тестов задач ingestion: подставной docparse, документ в базе, модель-заглушка."""

from typing import Any
from uuid import UUID

from clin_compass_knowledge.grls import Registry
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.core.ai.docparse import ParsedPage, ParsedPdf
from clin_compass.core.ai.llm import Llm
from clin_compass.core.storage import S3Storage
from clin_compass.modules.ingestion.analysis import Extractor
from clin_compass.modules.ingestion.models import Document
from clin_compass.modules.ingestion.tasks import Jobs
from tests.core.fake_nodes import MemoryRecorder
from tests.freetext.conftest import Care
from tests.freetext.helpers import Script
from tests.ingestion.helpers import MemoryStorage
from tests.ingestion.samples import pdf


class FakeParser:
    """docparse: отвечает страницами или ошибками по очереди, последний ответ повторяет."""

    def __init__(self, *answers: tuple[ParsedPage, ...] | Exception) -> None:
        self._answers = list(answers)
        self.calls: list[tuple[bytes, str]] = []

    async def parse_pdf(self, data: bytes, *, task: str) -> ParsedPdf:
        self.calls.append((data, task))
        answer = self._answers.pop(0) if len(self._answers) > 1 else self._answers[0]
        if isinstance(answer, Exception):
            raise answer
        return ParsedPdf(pages=answer)


def page(number: int, text: str, layer_text: str = "") -> ParsedPage:
    return ParsedPage(number=number, text=text, layer_text=layer_text)


def make_jobs(
    maker: async_sessionmaker[AsyncSession],
    registry: Registry,
    *,
    parser: FakeParser | None = None,
    answers: tuple[dict[str, Any] | Exception, ...] = (),
    storage: S3Storage | MemoryStorage | None = None,
) -> tuple[Jobs, Script]:
    """Задачи с подставным docparse и моделью, которая отвечает `answers` по очереди."""
    script = Script(*answers) if answers else Script({})
    extractor = Extractor(Llm(script.model, MemoryRecorder()), registry) if answers else None
    jobs = Jobs(maker, storage, parser, extractor)
    return jobs, script


async def add_document(
    maker: async_sessionmaker[AsyncSession],
    care: Care,
    storage: MemoryStorage,
    *,
    data: bytes | None = None,
    content_type: str = "application/pdf",
    **values: Any,
) -> bytes:
    """Документ пациента и его объект в хранилище; возвращает аргумент задачи — id документа."""
    body = data if data is not None else pdf()
    async with maker() as session:
        document = Document(
            patient_id=care.patient.id,
            uploaded_by=care.doctor.id,
            filename="history.pdf",
            content_type=content_type,
            size=len(body),
            sha256=str(len(storage.objects)).rjust(64, "0"),
            object_key=f"documents/{len(storage.objects)}",
            pages=1,
            **values,
        )
        session.add(document)
        await session.commit()
        storage.put(document.object_key, body, content_type)
        return str(document.id).encode()


async def load(maker: async_sessionmaker[AsyncSession], payload: bytes) -> Document:
    async with maker() as session:
        document = await session.get(Document, UUID(payload.decode()))
    assert document is not None
    return document
