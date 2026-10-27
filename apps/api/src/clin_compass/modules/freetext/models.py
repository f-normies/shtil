"""Таблицы модуля freetext (схема Postgres `freetext`, спека #32, раздел 3.2).

Медицинские данные физически не удаляются: DELETE и TRUNCATE разборов, а также любые
изменения подтверждённых находок запрещают триггеры БД (миграция dbfde2ea5d9c).
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from clin_compass.core.orm import Base, created_at, timestamp, uuid_pk

SCHEMA = "freetext"
USERS = "identity.users.id"
CATEGORIES = "category IN ('symptom', 'side_effect', 'adherence', 'medication')"


class Analysis(Base):
    """Разбор одного текста пациента: снимок текста и итоги двух этапов."""

    __tablename__ = "analyses"
    __table_args__ = (
        CheckConstraint("source_type IN ('diary', 'message')", name="source_type"),
        CheckConstraint("risk_failures >= 0 AND extract_failures >= 0", name="failures"),
        CheckConstraint(
            "(risk_checked_at IS NULL) = (risk_run_id IS NULL)", name="risk_check_complete"
        ),
        CheckConstraint(
            "(extracted_at IS NULL) = (extract_run_id IS NULL)", name="extraction_complete"
        ),
        UniqueConstraint("source_type", "source_id"),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = uuid_pk()
    patient_id: Mapped[UUID] = mapped_column(ForeignKey(USERS), index=True)
    source_type: Mapped[str] = mapped_column(String(16))
    # Запись дневника или сообщение чата. Без внешнего ключа: таблица чужого модуля.
    source_id: Mapped[UUID]
    transcript: Mapped[bool] = mapped_column(Boolean)
    # Снимок текста, который видела модель: по нему проверяются цитаты.
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at()
    # Время записи, если разбор поставлен позже неё (голосовое сообщение после расшифровки).
    # NULL — разбор поставлен в транзакции записи, её время — `created_at` (спека #96).
    recorded_at: Mapped[datetime | None] = timestamp(nullable=True)
    risk_checked_at: Mapped[datetime | None] = timestamp(nullable=True)
    risk_run_id: Mapped[UUID | None]
    extracted_at: Mapped[datetime | None] = timestamp(nullable=True)
    extract_run_id: Mapped[UUID | None]
    # Признаки риска обоих этапов: вид, цитата, найдена ли цитата, этап.
    risk_signs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default="[]")
    # Вид признака → id сигнала, который этот разбор поднял или к которому привязал признак.
    alerts: Mapped[dict[str, str]] = mapped_column(JSONB, server_default="{}")
    # Неудачные попытки этапа. Ожидание недоступного узла сюда не входит.
    risk_failures: Mapped[int] = mapped_column(Integer, server_default="0")
    extract_failures: Mapped[int] = mapped_column(Integer, server_default="0")
    risk_failed_at: Mapped[datetime | None] = timestamp(nullable=True)
    extract_failed_at: Mapped[datetime | None] = timestamp(nullable=True)


class Finding(Base):
    """Находка, подтверждённая врачом. Только дописывается."""

    __tablename__ = "findings"
    __table_args__ = (CheckConstraint(CATEGORIES, name="category"), {"schema": SCHEMA})

    id: Mapped[UUID] = uuid_pk()
    # Предложение, из которого пришла находка. Без внешнего ключа: таблица чужого модуля.
    proposal_id: Mapped[UUID] = mapped_column(unique=True)
    analysis_id: Mapped[UUID] = mapped_column(ForeignKey(f"{SCHEMA}.analyses.id"), index=True)
    patient_id: Mapped[UUID] = mapped_column(ForeignKey(USERS), index=True)
    category: Mapped[str] = mapped_column(String(16))
    data: Mapped[dict[str, Any]] = mapped_column(JSONB)
    confirmed_at: Mapped[datetime] = created_at()
    confirmed_by: Mapped[UUID] = mapped_column(ForeignKey(USERS))
