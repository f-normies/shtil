"""Таблицы модуля ingestion (схема Postgres `ingestion`, спека #30, раздел 4).

Медицинские данные физически не удаляются: DELETE и TRUNCATE документов, а также любые
изменения подтверждённых фактов запрещают триггеры БД (миграция d6920e66414f).
"""

from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from clin_compass.core.orm import Base, created_at, timestamp, uuid_pk

SCHEMA = "ingestion"
USERS = "identity.users.id"


class Document(Base):
    """Загруженная история болезни: файл в S3, снимок распознанного текста, итоги этапов."""

    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint("status IN ('parsing', 'extracting', 'done', 'failed')", name="status"),
        CheckConstraint(
            "content_type IN ('application/pdf', 'image/jpeg', 'image/png')", name="content_type"
        ),
        CheckConstraint("(status = 'failed') = (error_code IS NOT NULL)", name="error_status"),
        CheckConstraint("parse_failures >= 0 AND extract_failures >= 0", name="failures"),
        CheckConstraint(
            "(extracted_at IS NULL) = (extract_run_id IS NULL)", name="extraction_complete"
        ),
        UniqueConstraint("patient_id", "sha256"),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = uuid_pk()
    patient_id: Mapped[UUID] = mapped_column(ForeignKey(USERS))
    uploaded_by: Mapped[UUID] = mapped_column(ForeignKey(USERS))
    filename: Mapped[str] = mapped_column(String(200))
    # Тип по сигнатуре файла, а не по заголовку клиента.
    content_type: Mapped[str] = mapped_column(String(32))
    size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    object_key: Mapped[str] = mapped_column(String(200))
    pages: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), server_default="parsing")
    error_code: Mapped[str | None] = mapped_column(String(32))
    error_page: Mapped[int | None] = mapped_column(Integer)
    # Снимок текста, который видела модель: по нему проверяются цитаты.
    text: Mapped[str | None] = mapped_column(Text)
    # Источник и границы каждой страницы в `text`.
    page_sources: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default="[]")
    ocr: Mapped[bool | None] = mapped_column(Boolean)
    created_at: Mapped[datetime] = created_at()
    parsed_at: Mapped[datetime | None] = timestamp(nullable=True)
    extracted_at: Mapped[datetime | None] = timestamp(nullable=True)
    extract_run_id: Mapped[UUID | None]
    dropped: Mapped[int | None] = mapped_column(Integer)
    document_date: Mapped[date | None] = mapped_column(Date)
    # Факты документа, которые уже были в карточке: вид предложения и `payload`.
    known: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default="[]")
    # Неудачные попытки этапа. Ожидание недоступного узла или S3 сюда не входит.
    parse_failures: Mapped[int] = mapped_column(Integer, server_default="0")
    extract_failures: Mapped[int] = mapped_column(Integer, server_default="0")


class Fact(Base):
    """Факт документа, подтверждённый врачом. Только дописывается."""

    __tablename__ = "facts"
    __table_args__ = (
        CheckConstraint("field IN ('anamnesis', 'risk_factor', 'past_medication')", name="field"),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = uuid_pk()
    # Предложение, из которого пришёл факт. Без внешнего ключа: таблица чужого модуля.
    proposal_id: Mapped[UUID] = mapped_column(unique=True)
    document_id: Mapped[UUID] = mapped_column(ForeignKey(f"{SCHEMA}.documents.id"), index=True)
    patient_id: Mapped[UUID] = mapped_column(ForeignKey(USERS), index=True)
    field: Mapped[str] = mapped_column(String(16))
    data: Mapped[dict[str, Any]] = mapped_column(JSONB)
    confirmed_at: Mapped[datetime] = created_at()
    confirmed_by: Mapped[UUID] = mapped_column(ForeignKey(USERS))
