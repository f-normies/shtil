"""Помощники тестов ingestion: вход, загрузка файла, хранилище в памяти, задачи очереди."""

from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.core.storage import StorageUnavailableError, StoredObject
from clin_compass.modules.ingestion.models import Document
from tests.live import PASSWORD
from tests.messaging.test_voice_contracts import MemoryStorage

__all__ = ["BrokenStorage", "MemoryStorage", "document", "jobs", "login", "set_document", "upload"]


class BrokenStorage(MemoryStorage):
    """S3, который не принимает и не отдаёт объекты."""

    def put(self, key: str, data: bytes, content_type: str) -> None:
        raise StorageUnavailableError("S3 write failed")

    def get(self, key: str, *, byte_range: str | None = None) -> StoredObject:
        raise StorageUnavailableError("S3 read failed")


async def login(client: httpx.AsyncClient, email: str) -> None:
    client.cookies.clear()
    response = await client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200


async def upload(
    client: httpx.AsyncClient,
    patient_id: UUID,
    data: bytes,
    *,
    filename: str = "history.pdf",
    content_type: str = "application/pdf",
) -> httpx.Response:
    return await client.post(
        f"/api/v1/patients/{patient_id}/documents",
        files={"file": (filename, data, content_type)},
    )


async def jobs(maker: async_sessionmaker[AsyncSession]) -> list[tuple[str, bytes]]:
    """Задачи в очереди: точка входа и аргумент."""
    async with maker() as session:
        rows = await session.execute(
            text("SELECT entrypoint, payload FROM pgq.pgqueuer ORDER BY id")
        )
        return [(row[0], bytes(row[1])) for row in rows]


async def document(maker: async_sessionmaker[AsyncSession], document_id: UUID | str) -> Document:
    async with maker() as session:
        found = await session.get(Document, UUID(str(document_id)))
    assert found is not None
    return found


async def set_document(
    maker: async_sessionmaker[AsyncSession], document_id: UUID | str, **values: Any
) -> None:
    async with maker() as session:
        found = await session.get(Document, UUID(str(document_id)))
        assert found is not None
        for name, value in values.items():
            setattr(found, name, value)
        await session.commit()
