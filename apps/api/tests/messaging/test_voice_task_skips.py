"""Проверки причин пропуска разбора voice-транскрипта."""

from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.core.config import Settings
from clin_compass.modules.freetext.models import Analysis
from clin_compass.modules.messaging.models import Conversation, Message
from clin_compass.modules.messaging.views import message_out
from tests.core.fake_nodes import fake_node
from tests.messaging.conftest import MakeMessagingCare
from tests.messaging.test_voice_tasks import (
    ControlledS3,
    _configured,
    _handler,
    _patch_s3,
    _state,
    _voice_message,
)


@pytest.mark.parametrize(
    ("transcript", "skip_reason"),
    [
        ("", "empty_transcript"),
        (" \t\n ", "empty_transcript"),
        ("т" * 20_001, "transcript_too_long"),
    ],
    ids=["empty", "blank", "too-long"],
)
async def test_patient_voice_transcript_is_saved_without_freetext_when_skipped(
    settings: Settings,
    sessionmaker: async_sessionmaker[AsyncSession],
    make_messaging_care: MakeMessagingCare,
    monkeypatch: pytest.MonkeyPatch,
    transcript: str,
    skip_reason: str,
) -> None:
    token = str(uuid4())
    care = await make_messaging_care(
        f"voice-skip-doctor-{token}@example.com",
        f"voice-skip-patient-{token}@example.com",
    )
    message_id = await _voice_message(sessionmaker, care)
    _patch_s3(monkeypatch, ControlledS3())

    async with fake_node(lambda _: (200, {"text": transcript, "duration": 1.25})) as node:
        await _handler(_configured(settings, node.url), sessionmaker)(str(message_id).encode())

    assert (await _state(sessionmaker, message_id))[0:4] == (
        "done",
        None,
        0,
        transcript,
    )
    async with sessionmaker() as session:
        message = await session.get(Message, message_id)
        assert message is not None
        conversation = await session.get(Conversation, message.conversation_id)
        assert conversation is not None
        analyses = list(
            (
                await session.scalars(
                    select(Analysis).where(
                        Analysis.source_type == "message", Analysis.source_id == message_id
                    )
                )
            ).all()
        )
    assert analyses == []
    assert message_out(message, conversation, {}).analysis_skip_reason == skip_reason
