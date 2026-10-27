"""Проверки обработчика ASR: ожидание узлов, ошибки и атомарный результат."""

from io import BytesIO
from typing import Any
from uuid import UUID, uuid4

import boto3.session
import pytest
from botocore.exceptions import ClientError
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.core.ai.runs import AiRun
from clin_compass.core.config import Settings
from clin_compass.core.queue import Entrypoint, RetryRequested
from clin_compass.modules.freetext.models import Analysis
from clin_compass.modules.messaging.models import Conversation, Message
from clin_compass.worker import build_handlers
from tests.core.fake_nodes import dead_node_url, fake_node
from tests.messaging.conftest import MakeMessagingCare

AUDIO = b"\x1a\x45\xdf\xa3 synthetic webm"
TRANSCRIPT = "Тестовая расшифровка."


class ControlledS3:
    """Подменяет только S3 SDK transport, оставляя адаптер и его ошибки настоящими."""

    def __init__(self, *, error_code: str | None = None) -> None:
        self.error_code = error_code
        self.get_calls: list[dict[str, Any]] = []

    def get_object(self, **kwargs: Any) -> dict[str, Any]:
        self.get_calls.append(kwargs)
        if self.error_code is not None:
            raise ClientError(
                {"Error": {"Code": self.error_code, "Message": "private endpoint detail"}},
                "GetObject",
            )
        return {
            "Body": BytesIO(AUDIO),
            "ContentLength": len(AUDIO),
            "ContentType": "audio/webm",
        }


def _patch_s3(monkeypatch: pytest.MonkeyPatch, client: ControlledS3) -> None:
    class FakeSession:
        def __init__(self, **_: Any) -> None:
            pass

        def client(self, service_name: str, **_: Any) -> ControlledS3:
            assert service_name == "s3"
            return client

    monkeypatch.setattr(boto3.session, "Session", FakeSession)


def _configured(settings: Settings, *asr_urls: str) -> Settings:
    return settings.model_copy(
        update={
            "asr_urls": asr_urls,
            "s3_endpoint_url": "http://rustfs.test:9000",
            "s3_access_key": "test-access",
            "s3_secret_key": SecretStr("test-secret"),
            "s3_bucket": "private-audio",
            "s3_region": "us-east-1",
        }
    )


async def _voice_message(
    sessionmaker: async_sessionmaker[AsyncSession],
    care: Any,
    *,
    sender_id: UUID | None = None,
) -> UUID:
    async with sessionmaker() as session:
        conversation = Conversation(
            patient_id=care.patient.id, doctor_id=care.doctor.id, last_seq=1
        )
        session.add(conversation)
        await session.flush()
        message = Message(
            conversation_id=conversation.id,
            seq=1,
            sender_id=sender_id or care.patient.id,
            client_message_id=uuid4(),
            kind="voice",
            audio_key="messages/private-object-key",
            audio_mime="audio/webm",
            audio_sha256="a" * 64,
            audio_size=len(AUDIO),
            transcription_status="queued",
        )
        session.add(message)
        await session.flush()
        message_id = message.id
        await session.commit()
    return message_id


def _handler(settings: Settings, sessionmaker: async_sessionmaker[AsyncSession]):
    handlers = build_handlers(settings, sessionmaker)
    entry = handlers.get("messaging.transcribe")
    assert isinstance(entry, Entrypoint)
    assert entry.concurrency_limit == 1
    return entry.handler


async def _state(
    sessionmaker: async_sessionmaker[AsyncSession], message_id: UUID
) -> tuple[str | None, str | None, int, str | None, int | None, UUID | None]:
    async with sessionmaker() as session:
        message = await session.get(Message, message_id)
    assert message is not None
    return (
        message.transcription_status,
        message.transcription_error_code,
        message.transcription_failures,
        message.transcription_text,
        message.duration_ms,
        message.asr_run_id,
    )


async def test_empty_asr_configuration_waits_without_counting_failure(
    settings: Settings,
    sessionmaker: async_sessionmaker[AsyncSession],
    make_messaging_care: MakeMessagingCare,
) -> None:
    care = await make_messaging_care(
        "voice-offline-doctor@example.com", "voice-offline-patient@example.com"
    )
    message_id = await _voice_message(sessionmaker, care)

    with pytest.raises(RetryRequested) as retry:
        await _handler(settings, sessionmaker)(str(message_id).encode())

    assert retry.value.delay.total_seconds() == 30
    assert await _state(sessionmaker, message_id) == ("queued", None, 0, None, None, None)


async def test_patient_transcript_is_recorded_and_submitted_to_freetext_once(
    settings: Settings,
    sessionmaker: async_sessionmaker[AsyncSession],
    make_messaging_care: MakeMessagingCare,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    care = await make_messaging_care(
        "voice-result-doctor@example.com", "voice-result-patient@example.com"
    )
    message_id = await _voice_message(sessionmaker, care)
    s3 = ControlledS3()
    _patch_s3(monkeypatch, s3)

    async with fake_node(lambda _: (200, {"text": TRANSCRIPT, "duration": 3.62})) as node:
        handler = _handler(_configured(settings, node.url), sessionmaker)
        payload = str(message_id).encode()
        await handler(payload)
        await handler(payload)

    status, error, failures, transcript, duration, run_id = await _state(sessionmaker, message_id)
    assert (status, error, failures, transcript, duration) == ("done", None, 0, TRANSCRIPT, 3620)
    assert run_id is not None
    assert len(node.requests) == 1
    assert len(s3.get_calls) == 1
    async with sessionmaker() as session:
        analyses = list(
            (
                await session.scalars(
                    select(Analysis).where(
                        Analysis.source_type == "message", Analysis.source_id == message_id
                    )
                )
            ).all()
        )
        jobs = list(
            (await session.scalars(select(AiRun).where(AiRun.task == "messaging.transcribe"))).all()
        )
    assert len(analyses) == 1
    assert (analyses[0].text, analyses[0].transcript, analyses[0].patient_id) == (
        TRANSCRIPT,
        True,
        care.patient.id,
    )
    assert len(jobs) == 1
    assert (jobs[0].id, jobs[0].status, jobs[0].role) == (run_id, "ok", "asr")


async def test_doctor_transcript_never_enters_freetext(
    settings: Settings,
    sessionmaker: async_sessionmaker[AsyncSession],
    make_messaging_care: MakeMessagingCare,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    care = await make_messaging_care(
        "voice-doctor-result-doctor@example.com", "voice-doctor-result-patient@example.com"
    )
    message_id = await _voice_message(sessionmaker, care, sender_id=care.doctor.id)
    _patch_s3(monkeypatch, ControlledS3())

    async with fake_node(lambda _: (200, {"text": TRANSCRIPT, "duration": 1.0})) as node:
        await _handler(_configured(settings, node.url), sessionmaker)(str(message_id).encode())

    assert (await _state(sessionmaker, message_id))[0] == "done"
    async with sessionmaker() as session:
        analysis = await session.scalar(
            select(Analysis).where(
                Analysis.source_type == "message", Analysis.source_id == message_id
            )
        )
    assert analysis is None


async def test_rejected_audio_becomes_terminal_without_retrying(
    settings: Settings,
    sessionmaker: async_sessionmaker[AsyncSession],
    make_messaging_care: MakeMessagingCare,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    care = await make_messaging_care(
        "voice-reject-doctor@example.com", "voice-reject-patient@example.com"
    )
    message_id = await _voice_message(sessionmaker, care)
    _patch_s3(monkeypatch, ControlledS3())

    async with fake_node(lambda _: (415, {"detail": "unsupported"})) as node:
        await _handler(_configured(settings, node.url), sessionmaker)(str(message_id).encode())

    assert len(node.requests) == 1
    assert (await _state(sessionmaker, message_id))[:3] == ("failed", "asr_rejected", 0)


async def test_missing_s3_object_is_terminal_and_never_sent_to_asr(
    settings: Settings,
    sessionmaker: async_sessionmaker[AsyncSession],
    make_messaging_care: MakeMessagingCare,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    care = await make_messaging_care(
        "voice-object-gone-doctor@example.com", "voice-object-gone-patient@example.com"
    )
    message_id = await _voice_message(sessionmaker, care)
    _patch_s3(monkeypatch, ControlledS3(error_code="NoSuchKey"))

    await _handler(_configured(settings, dead_node_url()), sessionmaker)(str(message_id).encode())

    assert (await _state(sessionmaker, message_id))[:3] == ("failed", "audio_missing", 0)


async def test_nodes_unavailable_waits_without_counting_asr_failure(
    settings: Settings,
    sessionmaker: async_sessionmaker[AsyncSession],
    make_messaging_care: MakeMessagingCare,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    care = await make_messaging_care(
        "voice-nodes-down-doctor@example.com", "voice-nodes-down-patient@example.com"
    )
    message_id = await _voice_message(sessionmaker, care)
    _patch_s3(monkeypatch, ControlledS3())

    with pytest.raises(RetryRequested) as retry:
        await _handler(_configured(settings, dead_node_url()), sessionmaker)(
            str(message_id).encode()
        )

    assert retry.value.delay.total_seconds() == 30
    assert (await _state(sessionmaker, message_id))[:3] == ("processing", None, 0)


async def test_real_asr_errors_have_three_total_attempts_and_finite_delays(
    settings: Settings,
    sessionmaker: async_sessionmaker[AsyncSession],
    make_messaging_care: MakeMessagingCare,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    care = await make_messaging_care(
        "voice-retries-doctor@example.com", "voice-retries-patient@example.com"
    )
    message_id = await _voice_message(sessionmaker, care)
    _patch_s3(monkeypatch, ControlledS3())

    async with fake_node(lambda _: (500, {"detail": "private endpoint detail"})) as node:
        handler = _handler(_configured(settings, node.url), sessionmaker)
        delays: list[float] = []
        for _ in range(2):
            with pytest.raises(RetryRequested) as retry:
                await handler(str(message_id).encode())
            delays.append(retry.value.delay.total_seconds())
        await handler(str(message_id).encode())

    assert delays == [5, 20]
    assert len(node.requests) == 3
    assert (await _state(sessionmaker, message_id))[:3] == (
        "failed",
        "transcription_failed",
        3,
    )
