"""Приём документа: гонка одинаковых файлов и потерянный ответ COMMIT (спека #30, 3.2)."""

import hashlib
from typing import Any

import anyio.from_thread
import pytest
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.modules.ingestion import service as ingestion
from clin_compass.modules.ingestion.models import Document
from tests.freetext.conftest import Care
from tests.ingestion.helpers import MemoryStorage, jobs
from tests.ingestion.samples import pdf


class RacingStorage(MemoryStorage):
    """Пока объект пишется в S3, тот же файл успевает принять другой запрос."""

    def __init__(self, maker: async_sessionmaker[AsyncSession], care: Care, data: bytes) -> None:
        super().__init__()
        self._maker, self._care, self._data = maker, care, data

    def put(self, key: str, data: bytes, content_type: str) -> None:
        super().put(key, data, content_type)
        if len(self.objects) == 1:
            anyio.from_thread.run(self._winner)

    async def _winner(self) -> None:
        async with self._maker() as session:
            session.add(
                Document(
                    patient_id=self._care.patient.id,
                    uploaded_by=self._care.doctor.id,
                    filename="first.pdf",
                    content_type="application/pdf",
                    size=len(self._data),
                    sha256=hashlib.sha256(self._data).hexdigest(),
                    object_key="documents/winner",
                    pages=1,
                )
            )
            await session.commit()
        super().put("documents/winner", self._data, "application/pdf")


async def accept(
    maker: async_sessionmaker[AsyncSession], storage: Any, care: Care, data: bytes
) -> tuple[Any, bool]:
    async with maker() as session:
        return await ingestion.accept_document(
            session,
            maker,
            storage,
            patient_id=care.patient.id,
            doctor_id=care.doctor.id,
            filename="history.pdf",
            data=data,
        )


async def test_loser_of_a_race_returns_winner_and_deletes_only_its_object(
    sessionmaker: async_sessionmaker[AsyncSession], care: Care
) -> None:
    data = pdf()
    storage = RacingStorage(sessionmaker, care, data)

    document, created = await accept(sessionmaker, storage, care, data)

    assert (document.filename, created) == ("first.pdf", False)
    assert list(storage.objects) == ["documents/winner"]
    assert len(storage.delete_calls) == 1
    assert await jobs(sessionmaker) == []


async def test_failed_commit_deletes_object_of_the_attempt(
    sessionmaker: async_sessionmaker[AsyncSession], care: Care, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = MemoryStorage()

    async def lost(self: AsyncSession) -> None:
        raise OperationalError("COMMIT", {}, Exception("connection lost"))

    monkeypatch.setattr(AsyncSession, "commit", lost)
    with pytest.raises(ingestion.DocumentCommitUnknownError):
        await accept(sessionmaker, storage, care, pdf())
    monkeypatch.undo()

    assert storage.objects == {}
    assert len(storage.delete_calls) == 1


async def test_commit_whose_answer_was_lost_keeps_the_document(
    sessionmaker: async_sessionmaker[AsyncSession], care: Care, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = MemoryStorage()
    commit = AsyncSession.commit

    async def answer_lost(self: AsyncSession) -> None:
        await commit(self)
        raise OperationalError("COMMIT", {}, Exception("connection lost"))

    monkeypatch.setattr(AsyncSession, "commit", answer_lost)
    document, created = await accept(sessionmaker, storage, care, pdf())
    monkeypatch.undo()

    assert created is True
    assert document.status == "parsing"
    assert len(storage.objects) == 1
    assert storage.delete_calls == []
