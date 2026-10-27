"""Регистрация распознавания сообщений независимо от конфигурации узлов ASR."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.core.config import Settings
from clin_compass.core.queue import Entrypoint
from clin_compass.worker import build_handlers


def test_transcription_handler_is_registered_without_asr_or_llm_nodes(
    settings: Settings,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    handlers = build_handlers(settings, sessionmaker)

    handler = handlers.get("messaging.transcribe")
    assert isinstance(handler, Entrypoint)
    assert handler.concurrency_limit == 1
