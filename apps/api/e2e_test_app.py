"""Тестовая ASGI-фабрика для сквозного прогона с синтетическим объектным хранилищем."""

from io import BytesIO

from fastapi import FastAPI

import clin_compass.main as application
from clin_compass.core.config import Settings
from clin_compass.core.storage import StorageObjectMissingError, StoredObject


class _MemoryStorage:
    """Хранит только синтетические аудиофайлы процесса root e2e."""

    def __init__(self) -> None:
        self._objects: dict[str, tuple[bytes, str]] = {}

    def put(self, key: str, data: bytes, content_type: str) -> None:
        self._objects[key] = (data, content_type)

    def get(self, key: str, *, byte_range: str | None = None) -> StoredObject:
        try:
            data, content_type = self._objects[key]
        except KeyError:
            raise StorageObjectMissingError("S3 object is missing") from None
        content_range = None
        if byte_range is not None:
            first, last = byte_range.removeprefix("bytes=").split("-", 1)
            start = int(first)
            end = int(last) if last else len(data) - 1
            data = data[start : end + 1]
            content_range = f"bytes {start}-{end}/{len(self._objects[key][0])}"
        return StoredObject(BytesIO(data), len(data), content_type, content_range)

    def delete(self, key: str) -> None:
        self._objects.pop(key, None)


def _build_memory_storage(_settings: Settings) -> _MemoryStorage:
    return _MemoryStorage()


def create_test_app() -> FastAPI:
    """Create the production API with per-process synthetic audio storage."""
    application.build_storage = _build_memory_storage
    return application.create_app()
