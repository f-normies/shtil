"""Таблицы модуля assessments (схема Postgres `assessments`)."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from clin_compass.core.orm import Base, created_at, timestamp, uuid_pk

SCHEMA = "assessments"


class AssessmentResult(Base):
    """Неизменяемый результат заполнения с копиями определения и источников.

    Строки только добавляются: UPDATE, DELETE и TRUNCATE запрещены триггерами в БД.
    """

    __tablename__ = "results"
    __table_args__ = (
        UniqueConstraint(
            "patient_id", "submission_id", name="uq_assessments_results_patient_submission"
        ),
        Index("ix_assessments_results_history", "patient_id", "id"),
        # Базовый снимок пары: первый принятый результат шкалы и версии.
        Index("ix_assessments_results_definition", "scale_id", "scale_version", "id"),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = uuid_pk()
    patient_id: Mapped[UUID] = mapped_column(ForeignKey("identity.users.id"))
    submission_id: Mapped[UUID]
    scale_id: Mapped[str] = mapped_column(String(200))
    scale_version: Mapped[int]
    scale_title: Mapped[str] = mapped_column(Text)
    completed_at: Mapped[datetime] = timestamp()
    received_at: Mapped[datetime] = created_at()
    answers: Mapped[dict[str, Any]] = mapped_column(JSONB)
    scores: Mapped[dict[str, Any]] = mapped_column(JSONB)
    definition_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    sources_snapshot: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    # Врач, внёсший результат шкалы врача (#94); NULL — самоотчёт пациента.
    recorded_by: Mapped[UUID | None] = mapped_column(ForeignKey("identity.users.id"))
