"""Интеграционные контракты S3-приёма, идемпотентности и HTTP ranges."""

from io import BytesIO
from typing import Any
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.core.storage import StorageObjectMissingError, StoredObject
from clin_compass.modules.messaging.models import Message
from tests.messaging.conftest import MakeMessagingCare, login


class _TrackedBytesIO(BytesIO):
    """Поток позволяет проверить закрытие после отправки HTTP-ответа."""


class MemoryStorage:
    """Контролируемый закрытый объектный адаптер для HTTP-проверок."""

    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}
        self.put_calls: list[tuple[str, bytes, str]] = []
        self.get_calls: list[tuple[str, str | None, _TrackedBytesIO]] = []
        self.delete_calls: list[str] = []

    def put(self, key: str, data: bytes, content_type: str) -> None:
        self.put_calls.append((key, data, content_type))
        self.objects[key] = (data, content_type)

    def get(self, key: str, *, byte_range: str | None = None) -> StoredObject:
        try:
            data, content_type = self.objects[key]
        except KeyError:
            raise StorageObjectMissingError("S3 object is missing") from None
        selected = data
        content_range = None
        if byte_range is not None:
            _, spec = byte_range.split("=", 1)
            first, last = spec.split("-", 1)
            start = int(first)
            end = int(last) if last else len(data) - 1
            selected = data[start : end + 1]
            content_range = f"bytes {start}-{end}/{len(data)}"
        body = _TrackedBytesIO(selected)
        self.get_calls.append((key, byte_range, body))
        return StoredObject(body, len(selected), content_type, content_range)

    def delete(self, key: str) -> None:
        self.delete_calls.append(key)
        self.objects.pop(key, None)


async def _upload(
    client: httpx.AsyncClient,
    care: Any,
    *,
    client_message_id: Any | None = None,
    content: bytes = b"\x1aE\xdf\xa3voice",
    content_type: str = "audio/webm; codecs=opus",
) -> httpx.Response:
    return await client.post(
        f"/api/v1/patients/{care.patient.id}/messages/voice",
        data={
            "expected_doctor_id": str(care.doctor.id),
            "client_message_id": str(client_message_id or uuid4()),
        },
        files={"file": ("patient-supplied-name.html", content, content_type)},
    )


async def test_voice_message_persists_private_key_and_one_worker_job(
    app: Any,
    client: httpx.AsyncClient,
    make_messaging_care: MakeMessagingCare,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    care = await make_messaging_care(
        "voice-save-doctor@example.com", "voice-save-patient@example.com"
    )
    store = MemoryStorage()
    app.state.storage = store
    await login(client, care.patient.email)
    client_id = uuid4()

    response = await _upload(client, care, client_message_id=client_id)

    assert response.status_code == 201
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["kind"] == "voice"
    assert body["text"] is None
    assert body["audio_mime"] == "audio/webm"
    assert body["audio_size"] == len(b"\x1aE\xdf\xa3voice")
    assert body["transcription_status"] == "queued"
    assert "audio_key" not in body
    assert len(store.put_calls) == 1
    key, uploaded, mime = store.put_calls[0]
    assert key.startswith("messages/") and "patient-supplied-name" not in key
    assert uploaded == b"\x1aE\xdf\xa3voice"
    assert mime == "audio/webm"
    async with sessionmaker() as session:
        rows = list(
            (
                await session.scalars(select(Message).where(Message.sender_id == care.patient.id))
            ).all()
        )
        jobs = list(
            (await session.scalars(text("SELECT entrypoint FROM pgq.pgqueuer ORDER BY id"))).all()
        )
    assert len(rows) == 1
    assert rows[0].audio_key == key
    assert jobs == ["messaging.transcribe"]


async def test_voice_idempotency_reuses_record_and_rejects_different_audio(
    app: Any,
    client: httpx.AsyncClient,
    make_messaging_care: MakeMessagingCare,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    care = await make_messaging_care(
        "voice-replay-doctor@example.com", "voice-replay-patient@example.com"
    )
    store = MemoryStorage()
    app.state.storage = store
    await login(client, care.patient.email)
    client_id = uuid4()

    first = await _upload(client, care, client_message_id=client_id)
    replay = await _upload(client, care, client_message_id=client_id)
    conflict = await _upload(
        client,
        care,
        client_message_id=client_id,
        content=b"different audio",
        content_type="audio/mp4",
    )

    assert first.status_code == 201
    assert replay.status_code == 200
    assert replay.json()["id"] == first.json()["id"]
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "idempotency_conflict"
    async with sessionmaker() as session:
        rows = list(
            (
                await session.scalars(
                    select(Message).where(
                        Message.sender_id == care.patient.id,
                        Message.client_message_id == client_id,
                    )
                )
            ).all()
        )
        jobs = list(
            (await session.scalars(text("SELECT entrypoint FROM pgq.pgqueuer ORDER BY id"))).all()
        )
    assert len(rows) == 1
    assert jobs == ["messaging.transcribe"]
    assert rows[0].audio_key in store.objects
    assert all(key != rows[0].audio_key for key in store.delete_calls)


async def test_audio_endpoint_streams_single_range_with_private_headers_and_closes_body(
    app: Any,
    client: httpx.AsyncClient,
    make_messaging_care: MakeMessagingCare,
) -> None:
    care = await make_messaging_care(
        "voice-range-doctor@example.com", "voice-range-patient@example.com"
    )
    store = MemoryStorage()
    app.state.storage = store
    await login(client, care.patient.email)
    uploaded = await _upload(client, care)
    assert uploaded.status_code == 201
    message_id = uploaded.json()["id"]

    response = await client.get(
        f"/api/v1/patients/{care.patient.id}/messages/{message_id}/audio",
        headers={"Range": "bytes=1-3"},
    )

    assert response.status_code == 206
    assert response.content == b"E\xdf\xa3"
    assert response.headers["content-range"] == f"bytes 1-3/{len(b'\x1aE\xdf\xa3voice')}"
    assert response.headers["content-length"] == "3"
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-type"] == "audio/webm"
    assert "patient-supplied-name" not in response.headers["content-disposition"]
    assert store.get_calls[0][1] == "bytes=1-3"
    assert store.get_calls[0][2].closed


@pytest.mark.parametrize("range_header", ["bytes=0-", "bytes=-3"])
async def test_audio_endpoint_supports_open_and_suffix_ranges(
    app: Any,
    client: httpx.AsyncClient,
    make_messaging_care: MakeMessagingCare,
    range_header: str,
) -> None:
    care = await make_messaging_care(
        f"voice-range-{range_header.replace('=', '')}-doctor@example.com",
        f"voice-range-{range_header.replace('=', '')}-patient@example.com",
    )
    store = MemoryStorage()
    app.state.storage = store
    await login(client, care.patient.email)
    uploaded = await _upload(client, care, content=b"012345")
    assert uploaded.status_code == 201
    message_id = uploaded.json()["id"]

    response = await client.get(
        f"/api/v1/patients/{care.patient.id}/messages/{message_id}/audio",
        headers={"Range": range_header},
    )

    assert response.status_code == 206
    assert response.content == (b"012345" if range_header == "bytes=0-" else b"345")


# Конец раньше начала — некорректный синтаксис (RFC 9110, 14.1.1): заголовок игнорируется.
@pytest.mark.parametrize("range_header", ["bytes=0-1,4-5", "bytes=2-1"])
async def test_audio_endpoint_ignores_malformed_or_multiple_ranges(
    app: Any,
    client: httpx.AsyncClient,
    make_messaging_care: MakeMessagingCare,
    range_header: str,
) -> None:
    care = await make_messaging_care(
        "voice-full-doctor@example.com", "voice-full-patient@example.com"
    )
    store = MemoryStorage()
    app.state.storage = store
    await login(client, care.patient.email)
    uploaded = await _upload(client, care, content=b"012345")
    assert uploaded.status_code == 201
    message_id = uploaded.json()["id"]

    response = await client.get(
        f"/api/v1/patients/{care.patient.id}/messages/{message_id}/audio",
        headers={"Range": range_header},
    )

    assert response.status_code == 200
    assert response.content == b"012345"
    assert store.get_calls[-1][1] is None


async def test_audio_endpoint_returns_416_for_unsatisfiable_range(
    app: Any,
    client: httpx.AsyncClient,
    make_messaging_care: MakeMessagingCare,
) -> None:
    care = await make_messaging_care(
        "voice-416-doctor@example.com", "voice-416-patient@example.com"
    )
    store = MemoryStorage()
    app.state.storage = store
    await login(client, care.patient.email)
    uploaded = await _upload(client, care, content=b"012345")
    assert uploaded.status_code == 201
    message_id = uploaded.json()["id"]

    response = await client.get(
        f"/api/v1/patients/{care.patient.id}/messages/{message_id}/audio",
        headers={"Range": "bytes=9-12"},
    )

    assert response.status_code == 416
    assert response.headers["content-range"] == "bytes */6"
    assert response.headers["cache-control"] == "no-store"
    assert store.get_calls == []


async def test_audio_endpoint_masks_missing_private_object_as_storage_unavailable(
    app: Any,
    client: httpx.AsyncClient,
    make_messaging_care: MakeMessagingCare,
) -> None:
    care = await make_messaging_care(
        "voice-missing-doctor@example.com", "voice-missing-patient@example.com"
    )
    store = MemoryStorage()
    app.state.storage = store
    await login(client, care.patient.email)
    uploaded = await _upload(client, care)
    assert uploaded.status_code == 201
    message_id = uploaded.json()["id"]
    key = store.put_calls[0][0]
    store.objects.pop(key)

    response = await client.get(f"/api/v1/patients/{care.patient.id}/messages/{message_id}/audio")

    assert response.status_code == 503
    assert response.json()["code"] == "storage_unavailable"
    assert key not in response.text
    assert "rustfs" not in response.text.lower()


async def test_audio_endpoint_hides_another_patients_message_before_s3_read(
    app: Any,
    client: httpx.AsyncClient,
    make_messaging_care: MakeMessagingCare,
) -> None:
    owner = await make_messaging_care(
        "voice-owner-doctor@example.com", "voice-owner-patient@example.com"
    )
    other = await make_messaging_care(
        "voice-other-doctor@example.com", "voice-other-patient@example.com"
    )
    store = MemoryStorage()
    app.state.storage = store
    await login(client, owner.patient.email)
    uploaded = await _upload(client, owner)
    assert uploaded.status_code == 201
    await login(client, other.patient.email)

    response = await client.get(
        f"/api/v1/patients/{owner.patient.id}/messages/{uploaded.json()['id']}/audio"
    )

    assert response.status_code == 404
    assert store.get_calls == []
