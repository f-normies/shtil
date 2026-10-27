"""Фикстуры ingestion: выдуманный справочник therapy и снимок реестра, врач с пациентом.

Справочник — `tests/therapy/knowledge`: у `test-drug` противопоказание «ингибиторы МАО
и 2 недели после отмены» (моклобемид), в снимке реестра есть «Аурорикс» и «Маоин» с МНН
моклобемид, «Суматриптан».
"""

from pathlib import Path

import pytest
from clin_compass_knowledge.grls import Registry, load_registry
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.core.config import Settings
from clin_compass.modules.therapy.deps import Catalog
from tests.freetext.conftest import Care, make_care

KNOWLEDGE = Path(__file__).parents[1] / "therapy" / "knowledge"
REGISTRY = KNOWLEDGE / "sources" / "files" / "registry.csv"


@pytest.fixture(scope="session")
def settings(settings: Settings) -> Settings:
    """Приложение в тестах ingestion читает тестовый справочник препаратов."""
    return settings.model_copy(update={"knowledge_dir": KNOWLEDGE})


@pytest.fixture(scope="session")
def registry() -> Registry:
    return load_registry(REGISTRY)


@pytest.fixture(scope="session")
def catalog() -> Catalog:
    return Catalog.load(KNOWLEDGE)


@pytest.fixture
async def care(sessionmaker: async_sessionmaker[AsyncSession]) -> Care:
    return await make_care(sessionmaker, "doctor@example.com", "patient@example.com")
