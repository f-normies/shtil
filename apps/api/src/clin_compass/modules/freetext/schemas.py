"""Публичные схемы модуля freetext."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from clin_compass.modules.proposals.schemas import ProposalOut

# Откуда текст: запись дневника или сообщение пациента в чате.
SourceType = Literal["diary", "message"]
AnalysisStatus = Literal["processing", "done", "failed"]
FindingCategory = Literal["symptom", "side_effect", "adherence", "medication"]
AdherenceEvent = Literal["taken", "missed", "stopped", "dose_changed"]


class FindingOut(BaseModel):
    """Находка из текста пациента, подтверждённая врачом."""

    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    proposal_id: UUID
    analysis_id: UUID
    patient_id: UUID
    category: FindingCategory
    # Находка из предложения: формулировка, цитата, препарат с итогом сверки, событие приёма.
    data: dict[str, Any]
    confirmed_at: datetime
    confirmed_by: UUID


class DiarySourceOut(BaseModel):
    """Состояние разбора и результаты врача по одному источнику дневника."""

    model_config = ConfigDict(frozen=True)

    source_id: UUID
    status: AnalysisStatus
    proposals: list[ProposalOut]
    findings: list[FindingOut]
