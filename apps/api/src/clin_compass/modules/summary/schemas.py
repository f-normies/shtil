"""Публичные схемы модуля summary: сводка динамики пациента между визитами (спека #38, 3.5)."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from clin_compass.modules.alerts.schemas import AlertLevel

SummaryStatus = Literal["pending", "done", "failed", "confirmed", "empty"]
FactKind = Literal["scale", "signal", "finding"]
# Раздел карточки, который открывает ссылка факта.
FactSection = Literal["assessments", "signals", "diary", "messages"]
MAX_STATEMENTS = 5
MAX_STATEMENT_LENGTH = 500

StatementText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_STATEMENT_LENGTH)
]


class SummaryFact(BaseModel):
    """Факт из данных пациента за период: номер, вид, дата, текст и раздел карточки.

    `level` — уровень сигнала у фактов `signal`, у остальных None. Слово уровня есть
    и в тексте: его видит модель.
    """

    model_config = ConfigDict(frozen=True)

    number: int
    kind: FactKind
    at: datetime
    text: str
    section: FactSection
    level: AlertLevel | None


class SummaryStatement(BaseModel):
    """Утверждение сводки и номера фактов, на которых оно основано."""

    model_config = ConfigDict(frozen=True)

    text: StatementText
    facts: list[int] = Field(min_length=1)


class ConfirmSummaryIn(BaseModel):
    """Версия врача: 1–5 утверждений, номера фактов — из снимка черновика."""

    statements: list[SummaryStatement] = Field(min_length=1, max_length=MAX_STATEMENTS)


class VisitSummaryOut(BaseModel):
    """Текущая сводка пациента.

    `id` пуст при `empty`: фактов за период нет, сводки нет. `stale` — показан черновик
    по прежнему ключу данных, новый ещё считается или упал. `statements` — черновик системы
    или, после подтверждения, версия врача; `edited` — версия врача отличается от черновика.
    """

    model_config = ConfigDict(frozen=True)

    id: UUID | None
    status: SummaryStatus
    period_start: datetime
    period_end: datetime | None
    stale: bool
    facts: list[SummaryFact]
    statements: list[SummaryStatement]
    edited: bool
    confirmed_by: UUID | None
    confirmed_by_name: str | None
    confirmed_at: datetime | None
