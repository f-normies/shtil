"""Вид признака риска в свободном тексте: `signs/<id>.yaml` (спека #32, раздел 6.1).

Модель решает только, есть ли в тексте признак этого вида. Заголовок сигнала и действия
врача берутся отсюда: у каждого действия есть источник.
"""

from pydantic import Field

from clin_compass_knowledge.schemas.common import Id, Model, NonEmpty, SourceRef, Status


class Sign(Model):
    """Признак риска, который LLM ищет в дневнике и чате."""

    id: Id
    version: int = Field(ge=1)
    status: Status
    # Тяжесть: 1 — самый тяжёлый. Решает, чей заголовок у сигнала и когда нужен новый сигнал.
    rank: int = Field(ge=1)
    title: NonEmpty
    # Что считается признаком, с примерами формулировок. Подставляется в промпт.
    definition: NonEmpty
    doctor_actions: tuple[NonEmpty, ...] = Field(min_length=1)
    sources: tuple[SourceRef, ...] = Field(min_length=1)
