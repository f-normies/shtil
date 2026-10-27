"""Таблица предложений (схема Postgres `proposals`)."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from clin_compass.core.orm import Base, created_at, timestamp, uuid_pk

SCHEMA = "proposals"
USERS = "identity.users.id"


class Proposal(Base):
    """Клиническое действие, которое ждёт решения врача.

    Действовать начинает только после подтверждения: тогда модуль-владелец
    вида предложения применяет его в той же транзакции.
    """

    __tablename__ = "proposals"
    __table_args__ = (
        CheckConstraint("status IN ('proposed', 'confirmed', 'rejected')", name="status"),
        CheckConstraint("origin_type IN ('rule', 'ai_run', 'doctor')", name="origin_type"),
        CheckConstraint(
            "(status = 'proposed') = (decided_at IS NULL AND decided_by IS NULL)",
            name="decision_matches_status",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = uuid_pk()
    patient_id: Mapped[UUID] = mapped_column(ForeignKey(USERS), index=True)
    kind: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    origin_type: Mapped[str] = mapped_column(String(16))
    origin_ref: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(16), server_default="proposed")
    created_at: Mapped[datetime] = created_at()
    decided_by: Mapped[UUID | None] = mapped_column(ForeignKey(USERS))
    decided_at: Mapped[datetime | None] = timestamp(nullable=True)
    decision_comment: Mapped[str | None] = mapped_column(Text)
