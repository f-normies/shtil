"""Регрессии замечаний ревью голоса: терминальные повторы, доступ, COMMIT и freetext."""

import hashlib
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.modules.freetext import service as freetext
from clin_compass.modules.identity.models import AccessLogEntry
from clin_compass.modules.messaging.models import Conversation, Message
from clin_compass.modules.messaging.voice import _resolve_commit
from tests.messaging.conftest import MakeMessagingCare, login
from tests.messaging.test_voice_contracts import MemoryStorage, _upload
from tests.messaging.test_voice_tasks import _handler, _state, _voice_message

REPLAY_AUDIO = b"\x1aE\xdf\xa3voice"
REPLAY_TRANSCRIPT = "Голосовое сообщение пациента."


@pytest.mark.parametrize("terminal_status", ["done", "failed"], ids=["done", "failed"])
async def test_terminal_asr_message_is_noop_without_configured_dependencies(
    settings: Any,
    sessionmaker: async_sessionmaker[AsyncSession],
    make_messaging_care: MakeMessagingCare,
    terminal_status: str,
) -> None:
    care = await make_messaging_care(
        f"review-terminal-{terminal_status}-doctor@example.com",
        f"review-terminal-{terminal_status}-patient@example.com",
    )
    message_id = await _voice_message(sessionmaker, care)
    async with sessionmaker() as session:
        message = await session.get(Message, message_id)
        assert message is not None
        message.transcription_status = terminal_status
        message.transcription_error_code = (
            "transcription_failed" if terminal_status == "failed" else None
        )
        await session.commit()

    await _handler(settings, sessionmaker)(str(message_id).encode())

    assert (await _state(sessionmaker, message_id))[0] == terminal_status


@pytest.mark.parametrize(
    "content_type",
    ["application/octet-stream", "audio/webm"],
    ids=["unsupported-mime", "storage-unconfigured"],
)
async def test_voice_upload_hides_early_rejections_for_another_patient_and_audits_denial(
    client: httpx.AsyncClient,
    make_messaging_care: MakeMessagingCare,
    sessionmaker: async_sessionmaker[AsyncSession],
    content_type: str,
) -> None:
    owner = await make_messaging_care(
        "review-owner-doctor@example.com", "review-owner-patient@example.com"
    )
    other = await make_messaging_care(
        "review-other-doctor@example.com", "review-other-patient@example.com"
    )
    await login(client, owner.patient.email)

    response = await client.post(
        f"/api/v1/patients/{other.patient.id}/messages/voice",
        data={
            "expected_doctor_id": str(other.doctor.id),
            "client_message_id": str(uuid4()),
        },
        files={"file": ("recording.webm", b"synthetic audio", content_type)},
    )

    assert response.status_code == 404
    assert response.json()["code"] == "not_found"
    async with sessionmaker() as session:
        denied = await session.scalar(
            select(AccessLogEntry).where(
                AccessLogEntry.actor_id == owner.patient.id,
                AccessLogEntry.patient_id == other.patient.id,
                AccessLogEntry.action == "messaging.send.denied",
            )
        )
    assert denied is not None


class _SessionAwareStorage(MemoryStorage):
    def __init__(self, session_is_open: Callable[[], bool]) -> None:
        super().__init__()
        self.session_open_during_delete: list[bool] = []
        self._session_is_open = session_is_open

    def delete(self, key: str) -> None:
        self.session_open_during_delete.append(self._session_is_open())
        super().delete(key)


async def test_commit_resolution_deletes_losing_audio_after_check_session_closes(
    sessionmaker: async_sessionmaker[AsyncSession],
    make_messaging_care: MakeMessagingCare,
) -> None:
    care = await make_messaging_care(
        "review-commit-doctor@example.com", "review-commit-patient@example.com"
    )
    client_message_id = uuid4()
    digest = hashlib.sha256(REPLAY_AUDIO).hexdigest()
    async with sessionmaker() as session:
        conversation = Conversation(
            patient_id=care.patient.id, doctor_id=care.doctor.id, last_seq=1
        )
        session.add(conversation)
        await session.flush()
        session.add(
            Message(
                conversation_id=conversation.id,
                seq=1,
                sender_id=care.patient.id,
                client_message_id=client_message_id,
                kind="voice",
                audio_key="messages/winning-object",
                audio_mime="audio/webm",
                audio_sha256=digest,
                audio_size=len(REPLAY_AUDIO),
                transcription_status="queued",
            )
        )
        await session.commit()

    session_open = [False]

    @asynccontextmanager
    async def tracked_session() -> AsyncIterator[AsyncSession]:
        async with sessionmaker() as session:
            session_open[0] = True
            try:
                yield session
            finally:
                session_open[0] = False

    storage = _SessionAwareStorage(lambda: session_open[0])
    attempt_key = "messages/losing-object"
    storage.put(attempt_key, REPLAY_AUDIO, "audio/webm")

    result = await _resolve_commit(
        tracked_session,  # type: ignore[arg-type]
        storage,
        object_key=attempt_key,
        sender_id=care.patient.id,
        patient_id=care.patient.id,
        doctor_id=care.doctor.id,
        client_message_id=client_message_id,
        digest=digest,
        content_type="audio/webm",
    )

    assert result is not None and result[1] is False
    assert storage.session_open_during_delete == [False]
    assert storage.delete_calls == [attempt_key]


async def test_voice_idempotent_replay_returns_current_freetext_status(
    app: Any,
    client: httpx.AsyncClient,
    make_messaging_care: MakeMessagingCare,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    care = await make_messaging_care(
        "review-replay-doctor@example.com", "review-replay-patient@example.com"
    )
    client_message_id = uuid4()
    async with sessionmaker() as session:
        conversation = Conversation(
            patient_id=care.patient.id, doctor_id=care.doctor.id, last_seq=1
        )
        session.add(conversation)
        await session.flush()
        message = Message(
            conversation_id=conversation.id,
            seq=1,
            sender_id=care.patient.id,
            client_message_id=client_message_id,
            kind="voice",
            audio_key="messages/replay-object",
            audio_mime="audio/webm",
            audio_sha256=hashlib.sha256(REPLAY_AUDIO).hexdigest(),
            audio_size=len(REPLAY_AUDIO),
            transcription_status="done",
            transcription_text=REPLAY_TRANSCRIPT,
        )
        session.add(message)
        await session.flush()
        await freetext.submit_text(
            session,
            patient_id=care.patient.id,
            source_type="message",
            source_id=message.id,
            text=REPLAY_TRANSCRIPT,
            transcript=True,
        )
        await session.commit()

    app.state.storage = MemoryStorage()
    await login(client, care.patient.email)

    response = await _upload(
        client, care, client_message_id=client_message_id, content=REPLAY_AUDIO
    )

    assert response.status_code == 200
    assert response.json()["analysis_status"] == "processing"
