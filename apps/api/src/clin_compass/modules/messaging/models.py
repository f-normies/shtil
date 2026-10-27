"""Таблицы диалогов и сообщений в схеме Postgres `messaging`."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from clin_compass.core.orm import Base, created_at, uuid_pk

SCHEMA = "messaging"


class Conversation(Base):
    """История пары пациента и текущего ведущего врача."""

    __tablename__ = "conversations"
    __table_args__ = (
        UniqueConstraint("patient_id", "doctor_id", name="uq_messaging_conversations_pair"),
        CheckConstraint("last_seq >= 0", name="last_seq_nonnegative"),
        CheckConstraint("doctor_read_through_seq >= 0", name="doctor_read_nonnegative"),
        CheckConstraint("patient_read_through_seq >= 0", name="patient_read_nonnegative"),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = uuid_pk()
    patient_id: Mapped[UUID] = mapped_column(ForeignKey("patients.patients.user_id"), index=True)
    doctor_id: Mapped[UUID] = mapped_column(ForeignKey("identity.users.id"), index=True)
    last_seq: Mapped[int] = mapped_column(Integer, server_default="0")
    doctor_read_through_seq: Mapped[int] = mapped_column(Integer, server_default="0")
    patient_read_through_seq: Mapped[int] = mapped_column(Integer, server_default="0")
    created_at: Mapped[datetime] = created_at()


class Message(Base):
    """Неизменяемое сообщение с текстовыми и зарезервированными голосовыми полями."""

    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("conversation_id", "seq", name="uq_messaging_messages_conversation_seq"),
        UniqueConstraint(
            "sender_id", "client_message_id", name="uq_messaging_messages_sender_client"
        ),
        CheckConstraint("kind IN ('text', 'voice')", name="kind"),
        CheckConstraint(
            "(kind = 'text' AND text IS NOT NULL AND audio_key IS NULL) OR "
            "(kind = 'voice' AND text IS NULL AND audio_key IS NOT NULL)",
            name="content_matches_kind",
        ),
        CheckConstraint(
            "transcription_status IS NULL OR transcription_status IN "
            "('queued', 'processing', 'done', 'failed')",
            name="transcription_status",
        ),
        CheckConstraint(
            "transcription_error_code IS NULL OR transcription_error_code IN "
            "('asr_rejected', 'audio_missing', 'transcription_failed')",
            name="transcription_error_code",
        ),
        CheckConstraint("transcription_failures >= 0", name="transcription_failures_nonnegative"),
        CheckConstraint("audio_size IS NULL OR audio_size > 0", name="audio_size"),
        CheckConstraint("duration_ms IS NULL OR duration_ms >= 0", name="duration_ms"),
        Index("ix_messaging_messages_unread", "conversation_id", "sender_id", "seq"),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = uuid_pk()
    conversation_id: Mapped[UUID] = mapped_column(ForeignKey(f"{SCHEMA}.conversations.id"))
    seq: Mapped[int] = mapped_column(Integer)
    sender_id: Mapped[UUID] = mapped_column(ForeignKey("identity.users.id"))
    client_message_id: Mapped[UUID]
    kind: Mapped[str] = mapped_column(String(8))
    text: Mapped[str | None] = mapped_column(Text)
    audio_key: Mapped[str | None] = mapped_column(String(512))
    audio_mime: Mapped[str | None] = mapped_column(String(64))
    audio_sha256: Mapped[str | None] = mapped_column(String(64))
    audio_size: Mapped[int | None] = mapped_column(Integer)
    transcription_status: Mapped[str | None] = mapped_column(String(16))
    transcription_text: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    asr_run_id: Mapped[UUID | None]
    transcription_failures: Mapped[int] = mapped_column(Integer, server_default="0")
    transcription_error_code: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = created_at()


class PresenceLease(Base):
    """Аренда одного WebSocket-подключения, ограниченная сроком сессии."""

    __tablename__ = "presence_leases"
    __table_args__ = (
        Index("ix_messaging_presence_leases_user_expiry", "user_id", "lease_until"),
        Index("ix_messaging_presence_leases_session", "session_key"),
        {"schema": SCHEMA},
    )

    connection_id: Mapped[UUID] = uuid_pk()
    user_id: Mapped[UUID] = mapped_column(ForeignKey("identity.users.id"))
    session_key: Mapped[str] = mapped_column(String(64))
    lease_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class UserPresence(Base):
    """Последняя успешная проверка подключения, сохраняемая после закрытия сокета."""

    __tablename__ = "user_presence"
    __table_args__ = {"schema": SCHEMA}

    user_id: Mapped[UUID] = mapped_column(ForeignKey("identity.users.id"), primary_key=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TypingState(Base):
    """Короткая аренда ввода в паре от конкретного подключения."""

    __tablename__ = "typing_states"
    __table_args__ = (
        CheckConstraint("user_id = patient_id OR user_id = doctor_id", name="user_in_pair"),
        PrimaryKeyConstraint("connection_id", "patient_id", "doctor_id"),
        Index("ix_messaging_typing_pair_expiry", "patient_id", "doctor_id", "expires_at"),
        {"schema": SCHEMA},
    )

    connection_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.presence_leases.connection_id", ondelete="CASCADE")
    )
    patient_id: Mapped[UUID] = mapped_column(ForeignKey("patients.patients.user_id"))
    doctor_id: Mapped[UUID] = mapped_column(ForeignKey("identity.users.id"))
    user_id: Mapped[UUID] = mapped_column(ForeignKey("identity.users.id"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_active_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
