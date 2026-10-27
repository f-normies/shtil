"""Синтетические адаптеры и команды для HTTP-проверок голоса дневника."""

import json
from datetime import UTC, datetime
from fractions import Fraction
from io import BytesIO
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import av
import httpx

from clin_compass.core.storage import StorageObjectMissingError, StoredObject


def _synthetic_audio(container_format: str, codec: str) -> bytes:
    """Encode one quiet sample so tests exercise real container/codec tracks."""
    output = BytesIO()
    with av.open(output, mode="w", format=container_format) as container:
        stream = container.add_stream(codec, rate=48_000)
        stream.layout = "mono"
        frame = av.AudioFrame(format="fltp", layout="mono", samples=960)
        frame.sample_rate = 48_000
        frame.pts = 0
        frame.time_base = Fraction(1, 48_000)
        for plane in frame.planes:
            plane.update(bytes(plane.buffer_size))
        for packet in stream.encode(frame):
            container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)
    return output.getvalue()


WEBM = _synthetic_audio("webm", "libopus")
MP4 = _synthetic_audio("mp4", "aac")
MP4_ALAC = _synthetic_audio("mp4", "alac")
MOSCOW = ZoneInfo("Europe/Moscow")


class TrackedStream(BytesIO):
    """S3-подобный поток, закрытие которого можно проверить после HTTP-ответа."""


class MemoryStorage:
    """Закрытое S3-хранилище в памяти для интеграционных тестов."""

    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}
        self.put_calls: list[tuple[str, bytes, str]] = []
        self.get_calls: list[tuple[str, str | None, TrackedStream]] = []
        self.delete_calls: list[str] = []

    def put(self, key: str, data: bytes, content_type: str) -> None:
        self.put_calls.append((key, data, content_type))
        self.objects[key] = (data, content_type)

    def get(self, key: str, *, byte_range: str | None = None) -> StoredObject:
        try:
            data, content_type = self.objects[key]
        except KeyError:
            raise StorageObjectMissingError("S3 object is missing") from None
        content_range = None
        if byte_range is not None:
            _, value = byte_range.split("=", 1)
            start_text, end_text = value.split("-", 1)
            start = int(start_text)
            end = int(end_text) if end_text else len(data) - 1
            data = data[start : end + 1]
            content_range = f"bytes {start}-{end}/{len(self.objects[key][0])}"
        stream = TrackedStream(data)
        self.get_calls.append((key, byte_range, stream))
        return StoredObject(stream, len(data), content_type, content_range)

    def delete(self, key: str) -> None:
        self.delete_calls.append(key)
        self.objects.pop(key, None)


def voice_metadata(**updates: Any) -> dict[str, Any]:
    """Build a valid voice command with a client-measured recording duration."""
    now = datetime.now(UTC).astimezone(MOSCOW).replace(microsecond=0)
    return {
        "operation_id": str(uuid4()),
        "entry_id": str(uuid4()),
        "kind": "voice",
        "occurred_at": now.isoformat(),
        "duration_ms": 4_200,
        **updates,
    }


async def upload_voice(
    client: httpx.AsyncClient,
    metadata: dict[str, Any],
    *,
    audio: bytes = WEBM,
    content_type: str = "audio/webm; codecs=opus",
    filename: str = "patient-supplied-name.webm",
) -> httpx.Response:
    """Send command metadata as JSON and one audio file."""
    return await client.post(
        "/api/v1/diary/entries/voice",
        files={
            "metadata": (None, json.dumps(metadata, ensure_ascii=False), "application/json"),
            "file": (filename, audio, content_type),
        },
    )
