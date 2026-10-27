"""Таблицы модуля summary (схема Postgres `summary`, спека #38, 3.4)."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from clin_compass.core.orm import Base, created_at, timestamp, uuid_pk

SCHEMA = "summary"
USERS = "identity.users.id"


class VisitSummary(Base):
    """Сводка пациента за период по ключу данных. Строки не удаляются.

    `data_key` — SHA-256 от пациента, начала периода и фактов: данные поменялись — новая
    строка. `facts` — снимок фактов, которые видела модель; `draft` — её утверждения после
    проверки кодом; `confirmed_statements` — версия врача рядом с черновиком. У пациента
    в каждом периоде не больше одной подтверждённой сводки: подтверждение закрывает период.
    """

    __tablename__ = "visit_summaries"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'done', 'failed', 'confirmed')", name="status"),
        CheckConstraint("failures >= 0 AND dropped >= 0", name="counters"),
        CheckConstraint(
            "(status IN ('done', 'confirmed')) = (draft IS NOT NULL AND computed_at IS NOT NULL)",
            name="draft_ready",
        ),
        CheckConstraint(
            "(status = 'confirmed') = (confirmed_statements IS NOT NULL"
            " AND confirmed_by IS NOT NULL AND confirmed_at IS NOT NULL)",
            name="confirmed_complete",
        ),
        Index(
            "uq_visit_summaries_confirmed_period",
            "patient_id",
            "period_start",
            unique=True,
            postgresql_where=text("status = 'confirmed'"),
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = uuid_pk()
    patient_id: Mapped[UUID] = mapped_column(ForeignKey(USERS), index=True)
    period_start: Mapped[datetime] = timestamp()
    # Момент сбора фактов.
    period_end: Mapped[datetime] = timestamp()
    data_key: Mapped[str] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(16))
    # Неудачные попытки задачи. Ожидание недоступного узла сюда не входит.
    failures: Mapped[int] = mapped_column(Integer, server_default="0")
    facts: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    draft: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    # Утверждения модели, которые отбросила проверка.
    dropped: Mapped[int] = mapped_column(Integer, server_default="0")
    # Строка ai_runs вызова LLM. Без внешнего ключа: таблица ядра в другой схеме.
    run_id: Mapped[UUID | None]
    confirmed_statements: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    confirmed_by: Mapped[UUID | None] = mapped_column(ForeignKey(USERS))
    confirmed_at: Mapped[datetime | None] = timestamp(nullable=True)
    created_at: Mapped[datetime] = created_at()
    computed_at: Mapped[datetime | None] = timestamp(nullable=True)
