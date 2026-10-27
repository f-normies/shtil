"""Транзакционные границы голоса: конкурентный повтор, откат и исход COMMIT."""

import asyncio
import threading
from typing import Any
from uuid import uuid4

import anyio
import asyncpg
import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.core.storage import StorageUnavailableError
from clin_compass.modules.messaging.models import Message
from tests.messaging.conftest import MakeMessagingCare, login
from tests.messaging.test_voice_contracts import MemoryStorage, _upload


class BarrierStorage(MemoryStorage):
    """Дожидается обеих конкурентных попыток до возврата S3 put."""

    def __init__(self) -> None:
        super().__init__()
        self.barrier = threading.Barrier(2, timeout=10)

    def put(self, key: str, data: bytes, content_type: str) -> None:
        super().put(key, data, content_type)
        self.barrier.wait()


class FailingStorage(MemoryStorage):
    def put(self, key: str, data: bytes, content_type: str) -> None:
        raise StorageUnavailableError("S3 write failed")


async def _rows(
    sessionmaker: async_sessionmaker[AsyncSession], patient_id: Any
) -> tuple[list[Message], list[str]]:
    async with sessionmaker() as session:
        messages = list(
            (await session.scalars(select(Message).where(Message.sender_id == patient_id))).all()
        )
        jobs = list(
            (await session.scalars(text("SELECT entrypoint FROM pgq.pgqueuer ORDER BY id"))).all()
        )
    return messages, jobs


async def test_concurrent_idempotent_upload_keeps_only_the_winning_object(
    app: Any,
    client: httpx.AsyncClient,
    make_messaging_care: MakeMessagingCare,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    care = await make_messaging_care(
        "voice-race-doctor@example.com", "voice-race-patient@example.com"
    )
    storage = BarrierStorage()
    app.state.storage = storage
    await login(client, care.patient.email)
    client_id = uuid4()

    first, second = await asyncio.gather(
        _upload(client, care, client_message_id=client_id),
        _upload(client, care, client_message_id=client_id),
    )

    assert sorted([first.status_code, second.status_code]) == [200, 201]
    messages, jobs = await _rows(sessionmaker, care.patient.id)
    assert len(messages) == 1
    assert jobs == ["messaging.transcribe"]
    assert len(storage.put_calls) == 2
    winning_key = messages[0].audio_key
    attempted_keys = {key for key, _, _ in storage.put_calls}
    assert winning_key in storage.objects
    assert set(storage.delete_calls) == attempted_keys - {winning_key}


async def test_unknown_commit_resolves_committed_upload_on_a_fresh_session(
    app: Any,
    client: httpx.AsyncClient,
    make_messaging_care: MakeMessagingCare,
    sessionmaker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    care = await make_messaging_care(
        "voice-commit-known-doctor@example.com", "voice-commit-known-patient@example.com"
    )
    storage = MemoryStorage()
    app.state.storage = storage
    await login(client, care.patient.email)
    original_commit = AsyncSession.commit
    lost_reply = False

    async def commit_then_disconnect(session: AsyncSession) -> None:
        nonlocal lost_reply
        await original_commit(session)
        if not lost_reply:
            lost_reply = True
            raise OperationalError("COMMIT", {}, OSError("connection dropped"))

    monkeypatch.setattr(AsyncSession, "commit", commit_then_disconnect)

    response = await _upload(client, care, client_message_id=uuid4())

    assert response.status_code == 201
    messages, jobs = await _rows(sessionmaker, care.patient.id)
    assert len(messages) == 1
    assert jobs == ["messaging.transcribe"]
    assert messages[0].audio_key in storage.objects
    assert storage.delete_calls == []


async def test_unknown_commit_with_no_visible_row_keeps_attempt_object(
    app: Any,
    client: httpx.AsyncClient,
    make_messaging_care: MakeMessagingCare,
    sessionmaker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    care = await make_messaging_care(
        "voice-commit-unknown-doctor@example.com", "voice-commit-unknown-patient@example.com"
    )
    storage = MemoryStorage()
    app.state.storage = storage
    await login(client, care.patient.email)

    async def disconnect_before_commit(_: AsyncSession) -> None:
        raise OperationalError("COMMIT", {}, OSError("connection dropped"))

    monkeypatch.setattr(AsyncSession, "commit", disconnect_before_commit)

    response = await _upload(client, care, client_message_id=uuid4())

    assert response.status_code == 503
    assert response.json()["code"] == "messaging_unavailable"
    assert len(storage.put_calls) == 1
    attempted_key = storage.put_calls[0][0]
    assert attempted_key in storage.objects
    assert storage.delete_calls == []
    messages, jobs = await _rows(sessionmaker, care.patient.id)
    assert messages == []
    assert jobs == []


async def test_definite_transaction_failure_rolls_back_rows_and_deletes_own_attempt(
    app: Any,
    client: httpx.AsyncClient,
    make_messaging_care: MakeMessagingCare,
    sessionmaker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    care = await make_messaging_care(
        "voice-rollback-doctor@example.com", "voice-rollback-patient@example.com"
    )
    storage = MemoryStorage()
    app.state.storage = storage
    await login(client, care.patient.email)

    async def fail_enqueue(*_: Any, **__: Any) -> None:
        # pgqueuer пишет задачу через asyncpg напрямую, минуя исключения SQLAlchemy.
        raise asyncpg.exceptions.QueryCanceledError("canceling statement due to statement timeout")

    monkeypatch.setattr("clin_compass.modules.messaging.voice.queue.enqueue", fail_enqueue)

    with pytest.raises(asyncpg.exceptions.QueryCanceledError):
        await _upload(client, care)

    messages, jobs = await _rows(sessionmaker, care.patient.id)
    assert messages == []
    assert jobs == []
    assert len(storage.put_calls) == 1
    attempted_key = storage.put_calls[0][0]
    assert storage.delete_calls == [attempted_key]
    assert attempted_key not in storage.objects


async def test_cancelled_upload_before_commit_deletes_own_attempt(
    app: Any,
    client: httpx.AsyncClient,
    make_messaging_care: MakeMessagingCare,
    sessionmaker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    care = await make_messaging_care(
        "voice-cancel-doctor@example.com", "voice-cancel-patient@example.com"
    )
    storage = MemoryStorage()
    app.state.storage = storage
    await login(client, care.patient.email)
    enqueue_started = anyio.Event()

    async def hang_enqueue(*_: Any, **__: Any) -> None:
        enqueue_started.set()
        await anyio.sleep_forever()

    monkeypatch.setattr("clin_compass.modules.messaging.voice.queue.enqueue", hang_enqueue)

    # Отмена anyio повторяется на каждом await: без защиты откат и удаление не выполнятся.
    async with anyio.create_task_group() as group:
        group.start_soon(_upload, client, care)
        await enqueue_started.wait()
        group.cancel_scope.cancel()

    messages, jobs = await _rows(sessionmaker, care.patient.id)
    assert messages == []
    assert jobs == []
    assert len(storage.put_calls) == 1
    attempted_key = storage.put_calls[0][0]
    assert storage.delete_calls == [attempted_key]
    assert attempted_key not in storage.objects


async def test_s3_write_failure_creates_no_message_or_worker_job(
    app: Any,
    client: httpx.AsyncClient,
    make_messaging_care: MakeMessagingCare,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    care = await make_messaging_care(
        "voice-s3-fail-doctor@example.com", "voice-s3-fail-patient@example.com"
    )
    app.state.storage = FailingStorage()
    await login(client, care.patient.email)

    response = await _upload(client, care)

    assert response.status_code == 503
    assert response.json()["code"] == "storage_unavailable"
    messages, jobs = await _rows(sessionmaker, care.patient.id)
    assert messages == []
    assert jobs == []
