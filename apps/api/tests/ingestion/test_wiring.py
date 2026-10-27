"""Задачи ingestion регистрируются в worker'е только при заданных адресах узлов (спека #30, 3.7)."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.core.config import Settings
from clin_compass.core.queue import Entrypoint
from clin_compass.worker import build_handlers


def _ingestion(settings: Settings, maker: async_sessionmaker[AsyncSession]) -> dict[str, object]:
    return {n: h for n, h in build_handlers(settings, maker).items() if n.startswith("ingestion.")}


def test_without_nodes_documents_wait_in_queue(
    settings: Settings, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    assert _ingestion(settings, sessionmaker) == {}


def test_docparse_nodes_register_parsing_only(
    settings: Settings, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    configured = settings.model_copy(update={"docparse_urls": ("http://127.0.0.1:8003",)})

    handlers = _ingestion(configured, sessionmaker)

    assert list(handlers) == ["ingestion.parse"]


def test_both_nodes_register_both_stages_with_one_slot_each(
    settings: Settings, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    configured = settings.model_copy(
        update={
            "docparse_urls": ("http://127.0.0.1:8003",),
            "llm_urls": ("http://127.0.0.1:8000",),
            # Виды признаков риска для freetext: в справочнике therapy их нет.
            "knowledge_dir": None,
        }
    )

    handlers = _ingestion(configured, sessionmaker)

    assert sorted(handlers) == ["ingestion.extract", "ingestion.parse"]
    assert all(isinstance(h, Entrypoint) and h.concurrency_limit == 1 for h in handlers.values())
