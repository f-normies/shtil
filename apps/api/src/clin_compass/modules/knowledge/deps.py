"""Публичные зависимости FastAPI модуля knowledge: клиенты моделей и справочник источников."""

from typing import Annotated

from fastapi import Depends, Request

from clin_compass.modules.knowledge.service import KnowledgeAi, SourceCatalog


def get_ai(request: Request) -> KnowledgeAi | None:
    """Клиенты моделей, собранные при старте api; None — узлы эмбеддингов или LLM не заданы."""
    ai: KnowledgeAi | None = getattr(request.app.state, "knowledge_ai", None)
    return ai


def get_sources(request: Request) -> SourceCatalog:
    """Справочник источников, собранный при старте api из снимка knowledge/ (спека #106)."""
    sources: SourceCatalog = request.app.state.knowledge_sources
    return sources


KnowledgeAiDep = Annotated[KnowledgeAi | None, Depends(get_ai)]
KnowledgeSourcesDep = Annotated[SourceCatalog, Depends(get_sources)]

__all__ = [
    "KnowledgeAi",
    "KnowledgeAiDep",
    "KnowledgeSourcesDep",
    "SourceCatalog",
    "get_ai",
    "get_sources",
]
