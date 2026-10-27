"""Таблицы модуля identity (схема Postgres `identity`)."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from clin_compass.core.orm import Base, created_at, timestamp, uuid_pk

SCHEMA = "identity"


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IN ('doctor', 'patient', 'admin')", name="role"),
        CheckConstraint("email = lower(email)", name="email_lowercase"),
        # `disable_user` ставит время и причину вместе; без причины вход не знал бы, что ответить.
        CheckConstraint(
            "(disabled_at IS NULL) = (disabled_reason IS NULL)", name="disabled_complete"
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = uuid_pk()
    email: Mapped[str] = mapped_column(String(320), unique=True)
    password_hash: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(16))
    full_name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = created_at()
    # Вход закрыт (спека #112): с какого момента и почему (`DisabledReason`).
    disabled_at: Mapped[datetime | None] = timestamp(nullable=True)
    disabled_reason: Mapped[str | None] = mapped_column(String(32))


class AuthSession(Base):
    """Серверная сессия. В БД хранится только SHA-256 токена, сам токен — в cookie."""

    __tablename__ = "sessions"
    __table_args__ = {"schema": SCHEMA}

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey(f"{SCHEMA}.users.id"), index=True)
    created_at: Mapped[datetime] = created_at()
    expires_at: Mapped[datetime] = timestamp()
    revoked_at: Mapped[datetime | None] = timestamp(nullable=True)


class AccessLogEntry(Base):
    """Журнал доступа к данным пациентов.

    Только дописывается: UPDATE и DELETE запрещены триггером в БД.
    """

    __tablename__ = "access_log"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    actor_id: Mapped[UUID] = mapped_column(ForeignKey(f"{SCHEMA}.users.id"), index=True)
    action: Mapped[str] = mapped_column(String(64))
    patient_id: Mapped[UUID | None] = mapped_column(index=True)
    subject: Mapped[str | None] = mapped_column(String(200))
    at: Mapped[datetime] = created_at()


class ThrottleEvent(Base):
    """Событие для лимита частоты (спека #23): неудачный вход, приглашение, регистрация.

    Ключ — SHA-256: адресов почты и IP в таблице нет. Старые события удаляет `throttle.record`.
    """

    __tablename__ = "throttle_events"
    __table_args__ = (
        Index("ix_identity_throttle_events_bucket_key_at", "bucket", "key_hash", "at"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    bucket: Mapped[str] = mapped_column(String(32))
    key_hash: Mapped[str] = mapped_column(String(64))
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
