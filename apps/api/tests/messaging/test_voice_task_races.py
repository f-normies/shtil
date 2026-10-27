"""Гонки ASR и транзакционная связь расшифровки с freetext."""

import hashlib
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.core.ai.runs import AiRun
from clin_compass.core.config import Settings
from clin_compass.core.queue import RetryRequested
from clin_compass.modules.freetext import service as freetext
from clin_compass.modules.freetext.models import Analysis
from clin_compass.modules.messaging.models import Message
from tests.core.fake_nodes import fake_node
from tests.messaging.conftest import MakeMessagingCare
from tests.messaging.test_voice_tasks import (
    AUDIO,
    TRANSCRIPT,
    ControlledS3,
    _configured,
    _handler,
    _patch_s3,
    _state,
    _voice_message,
)


@pytest.mark.parametrize(
    ("terminal_status", "asr_status"),
    [("done", 200), ("failed", 200), ("done", 500)],
    ids=["late-success-keeps-done", "late-success-keeps-failed", "late-error-keeps-done"],
)
async def test_late_asr_result_preserves_terminal_message(
    settings: Settings,
    sessionmaker: async_sessionmaker[AsyncSession],
    make_messaging_care: MakeMessagingCare,
    monkeypatch: pytest.MonkeyPatch,
    terminal_status: str,
    asr_status: int,
) -> None:
    care = await make_messaging_care(
        f"voice-race-{terminal_status}-{asr_status}-{uuid4()}@example.com",
        f"voice-race-patient-{uuid4()}@example.com",
    )
    message_id = await _voice_message(sessionmaker, care)
    _patch_s3(monkeypatch, ControlledS3())
    winner_run_id = uuid4()

    async def finish_before_response(_: Any) -> tuple[int, dict[str, Any]]:
        async with sessionmaker.begin() as session:
            message = await session.get(Message, message_id)
            assert message is not None
            message.transcription_status = terminal_status
            message.transcription_text = "Закреплённая расшифровка."
            message.duration_ms = 5400
            message.asr_run_id = winner_run_id
            message.transcription_error_code = (
                "asr_rejected" if terminal_status == "failed" else None
            )
        payload = (
            {"text": TRANSCRIPT, "duration": 1.0}
            if asr_status == 200
            else {"detail": "private endpoint detail"}
        )
        return asr_status, payload

    async with fake_node(finish_before_response) as node:
        await _handler(_configured(settings, node.url), sessionmaker)(str(message_id).encode())

    assert await _state(sessionmaker, message_id) == (
        terminal_status,
        "asr_rejected" if terminal_status == "failed" else None,
        0,
        "Закреплённая расшифровка.",
        5400,
        winner_run_id,
    )


async def test_patient_transcript_rolls_back_with_message_when_freetext_fails(
    settings: Settings,
    sessionmaker: async_sessionmaker[AsyncSession],
    make_messaging_care: MakeMessagingCare,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    care = await make_messaging_care(
        "voice-atomic-doctor@example.com", "voice-atomic-patient@example.com"
    )
    message_id = await _voice_message(sessionmaker, care)
    _patch_s3(monkeypatch, ControlledS3())
    submit_text = freetext.submit_text

    async def fail_after_enqueue(*args: Any, **kwargs: Any) -> Any:
        await submit_text(*args, **kwargs)
        raise SQLAlchemyError("injected database write failure")

    monkeypatch.setattr(freetext, "submit_text", fail_after_enqueue)
    async with fake_node(lambda _: (200, {"text": TRANSCRIPT, "duration": 3.62})) as node:
        with pytest.raises(RetryRequested) as retry:
            await _handler(_configured(settings, node.url), sessionmaker)(str(message_id).encode())

    assert retry.value.delay.total_seconds() == 30
    assert await _state(sessionmaker, message_id) == ("processing", None, 0, None, None, None)
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
        runs = list(
            (
                await session.scalars(
                    select(AiRun).where(
                        AiRun.task == "messaging.transcribe",
                        AiRun.input_hash == hashlib.sha256(AUDIO).hexdigest(),
                    )
                )
            ).all()
        )
    assert analyses == []
    assert len(runs) == 1
