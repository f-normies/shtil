"""Публичные схемы модуля proposals."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

ProposalStatus = Literal["proposed", "confirmed", "rejected"]
OriginType = Literal["rule", "ai_run", "doctor"]


class ProposalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    patient_id: UUID
    kind: str
    payload: dict[str, Any]
    origin_type: OriginType
    origin_ref: str
    status: ProposalStatus
    created_at: datetime
    decided_by: UUID | None
    decided_at: datetime | None
    decision_comment: str | None


class DecisionRequest(BaseModel):
    comment: str | None = Field(default=None, max_length=2000)


class RunInfo(BaseModel):
    """AI-вызов, из которого пришло предложение: модель, версия промпта, начало вызова."""

    model_config = ConfigDict(from_attributes=True, frozen=True)

    model: str
    prompt_version: str | None
    created_at: datetime


class QueueItemOut(ProposalOut):
    """Предложение в очереди врача (спека #96, раздел 3.1).

    `run` есть только у предложения из `ai_runs`, если строка вызова нашлась.
    """

    patient_name: str
    run: RunInfo | None


class QueuePage(BaseModel):
    """Страница очереди. `next_cursor` — для следующей страницы; None — страница последняя."""

    model_config = ConfigDict(frozen=True)

    items: list[QueueItemOut]
    next_cursor: str | None


class QueueSummary(BaseModel):
    """Прогресс разбора: ждут решения и решены с начала дня врача (спека #96, раздел 3.4)."""

    model_config = ConfigDict(frozen=True)

    pending: int
    decided_today: int
