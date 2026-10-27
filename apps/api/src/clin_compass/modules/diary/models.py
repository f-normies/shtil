"""Таблицы дневника и неизменяемых версий (схема Postgres `diary`)."""

from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from clin_compass.core.orm import Base, created_at, timestamp, uuid_pk

SCHEMA = "diary"
USERS = "identity.users.id"


class Entry(Base):
    """Текущая видимая запись пациента; её версии хранятся в `Revision`."""

    __tablename__ = "entries"
    __table_args__ = (
        CheckConstraint("kind IN ('daily', 'mood', 'text', 'voice')", name="kind"),
        CheckConstraint("(kind = 'daily') = (daily_on IS NOT NULL)", name="daily_on"),
        CheckConstraint("current_revision >= 1", name="current_revision"),
        UniqueConstraint("patient_id", "daily_on", name="patient_daily"),
        Index("ix_diary_entries_feed", "patient_id", "occurred_at", "id"),
        Index("ix_diary_entries_month", "patient_id", "daily_on"),
        {"schema": SCHEMA},
    )

    # Клиент создаёт UUID до первой отправки, в том числе пока устройство offline.
    id: Mapped[UUID] = mapped_column(primary_key=True)
    patient_id: Mapped[UUID] = mapped_column(ForeignKey(USERS), index=True)
    kind: Mapped[str] = mapped_column(String(12))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    daily_on: Mapped[date | None] = mapped_column(Date)
    current_revision: Mapped[int] = mapped_column(Integer)
    received_at: Mapped[datetime] = created_at()
    hidden_at: Mapped[datetime | None] = timestamp(nullable=True)


class Audio(Base):
    """Закрытый аудиообъект и неизменённый результат ASR для voice-задачи."""

    __tablename__ = "audio"
    __table_args__ = (
        CheckConstraint("byte_size > 0", name="byte_size_positive"),
        CheckConstraint("duration_ms > 0", name="duration_positive"),
        CheckConstraint("failures >= 0", name="failures_nonnegative"),
        CheckConstraint("status IN ('pending', 'processing', 'done', 'failed')", name="status"),
        UniqueConstraint("storage_key", name="storage_key"),
        Index("ix_diary_audio_entry", "entry_id"),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = uuid_pk()
    entry_id: Mapped[UUID] = mapped_column(ForeignKey(f"{SCHEMA}.entries.id"))
    storage_key: Mapped[str] = mapped_column(String(500))
    sha256: Mapped[str] = mapped_column(String(64))
    byte_size: Mapped[int] = mapped_column(Integer)
    mime_type: Mapped[str] = mapped_column(String(100))
    duration_ms: Mapped[int] = mapped_column(Integer)
    transcript: Mapped[str | None] = mapped_column(Text)
    transcript_source_id: Mapped[UUID | None]
    asr_run_id: Mapped[UUID | None]
    status: Mapped[str] = mapped_column(String(16), server_default="pending")
    failures: Mapped[int] = mapped_column(Integer, server_default="0")
    received_at: Mapped[datetime] = created_at()


class Revision(Base):
    """Неизменяемый снимок полей записи и UUID источников текста."""

    __tablename__ = "revisions"
    __table_args__ = (
        CheckConstraint("revision >= 1", name="revision_positive"),
        CheckConstraint("mood_level IS NULL OR mood_level BETWEEN 1 AND 5", name="mood_level"),
        UniqueConstraint("entry_id", "revision", name="entry_revision"),
        Index("ix_diary_revisions_entry", "entry_id", "revision"),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = uuid_pk()
    entry_id: Mapped[UUID] = mapped_column(ForeignKey(f"{SCHEMA}.entries.id"))
    revision: Mapped[int] = mapped_column(Integer)
    mood_level: Mapped[int | None] = mapped_column(Integer)
    mood_words: Mapped[list[str]] = mapped_column(JSONB, server_default="[]")
    text: Mapped[str | None] = mapped_column(Text)
    text_source_id: Mapped[UUID | None]
    other_medicine_text: Mapped[str | None] = mapped_column(Text)
    other_medicine_source_id: Mapped[UUID | None]
    audio_id: Mapped[UUID | None] = mapped_column(ForeignKey(f"{SCHEMA}.audio.id"))
    created_at: Mapped[datetime] = created_at()


class Operation(Base):
    """Стабильная квитанция команды, уникальная для пациента и UUID операции."""

    __tablename__ = "operations"
    __table_args__ = (
        UniqueConstraint("patient_id", "operation_id", name="patient_operation"),
        Index("ix_diary_operations_patient", "patient_id", "created_at"),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = uuid_pk()
    patient_id: Mapped[UUID] = mapped_column(ForeignKey(USERS))
    operation_id: Mapped[UUID]
    operation_type: Mapped[str] = mapped_column(String(32))
    request_hash: Mapped[str] = mapped_column(String(64))
    receipt: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = created_at()


class IntakeMark(Base):
    """Версия явной отметки пациента о приёме назначенной дозы."""

    __tablename__ = "intake_marks"
    __table_args__ = (
        CheckConstraint("dose_ordinal >= 1", name="dose_ordinal"),
        CheckConstraint("version >= 1", name="version"),
        CheckConstraint("status IN ('taken', 'unmarked')", name="status"),
        CheckConstraint("source = 'patient_report'", name="source"),
        UniqueConstraint(
            "patient_id",
            "prescription_id",
            "scheduled_on",
            "dose_ordinal",
            "version",
            name="slot_version",
        ),
        Index("ix_diary_intake_patient_day", "patient_id", "scheduled_on"),
        Index(
            "ix_diary_intake_slot",
            "patient_id",
            "prescription_id",
            "scheduled_on",
            "dose_ordinal",
            "version",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = uuid_pk()
    patient_id: Mapped[UUID] = mapped_column(ForeignKey(USERS))
    prescription_id: Mapped[UUID] = mapped_column(ForeignKey("therapy.prescriptions.id"))
    scheduled_on: Mapped[date] = mapped_column(Date)
    dose_ordinal: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(12))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(String(24), server_default="patient_report")
    version: Mapped[int] = mapped_column(Integer)
    scheme_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    received_at: Mapped[datetime] = created_at()


class RevisionIntakeMark(Base):
    """Неизменяемая ссылка версии дневного итога на версии отметок приёма."""

    __tablename__ = "revision_intake_marks"
    __table_args__ = ({"schema": SCHEMA},)

    revision_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.revisions.id"), primary_key=True
    )
    intake_mark_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.intake_marks.id"), primary_key=True
    )
