"""Таблицы модуля patients (схема Postgres `patients`)."""

from datetime import date, datetime
from datetime import time as time_of_day
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Time,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from clin_compass.core.orm import Base, created_at, timestamp, uuid_pk

SCHEMA = "patients"
USERS = "identity.users.id"


class Invite(Base):
    """Приглашение врача. Токен одноразовый, в БД — только его SHA-256."""

    __tablename__ = "invites"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[UUID] = uuid_pk()
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    doctor_id: Mapped[UUID] = mapped_column(ForeignKey(USERS), index=True)
    created_at: Mapped[datetime] = created_at()
    expires_at: Mapped[datetime] = timestamp()
    used_at: Mapped[datetime | None] = timestamp(nullable=True)
    used_by: Mapped[UUID | None] = mapped_column(ForeignKey(USERS))


class Patient(Base):
    """Пациент и его ведущий врач. id пациента совпадает с id его пользователя."""

    __tablename__ = "patients"
    __table_args__ = {"schema": SCHEMA}

    user_id: Mapped[UUID] = mapped_column(ForeignKey(USERS), primary_key=True)
    lead_doctor_id: Mapped[UUID] = mapped_column(ForeignKey(USERS), index=True)
    created_at: Mapped[datetime] = created_at()
    # E.164, вводит ведущий врач (спека #13). Персональные данные: в логи не пишем.
    phone: Mapped[str | None] = mapped_column(String(16))
    # Клинический профиль, вносит врач (спека #28): нужен для проверки возраста по инструкции.
    birth_date: Mapped[date | None] = mapped_column(Date)
    sex: Mapped[str | None] = mapped_column(String(8))
    # Данные для противопоказаний (спека #29). `None` — не уточнено: проверка спросит врача.
    pregnant: Mapped[bool | None] = mapped_column(Boolean)
    breastfeeding: Mapped[bool | None] = mapped_column(Boolean)
    egfr: Mapped[float | None] = mapped_column(Float)
    egfr_measured_on: Mapped[date | None] = mapped_column(Date)
    child_pugh: Mapped[str | None] = mapped_column(String(8))


class Consent(Base):
    """Согласие пациента (152-ФЗ: данные о здоровье — специальная категория)."""

    __tablename__ = "consents"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[UUID] = uuid_pk()
    patient_id: Mapped[UUID] = mapped_column(ForeignKey(f"{SCHEMA}.patients.user_id"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    version: Mapped[str] = mapped_column(String(32))
    given_at: Mapped[datetime] = created_at()
    # Пациент отозвал согласие (спека #112). Строка не удаляется.
    revoked_at: Mapped[datetime | None] = timestamp(nullable=True)


class PatientPreferences(Base):
    """Настройки интерфейса пациента. Отсутствующая строка означает исходные значения."""

    __tablename__ = "preferences"
    __table_args__ = (
        CheckConstraint("btrim(display_name) <> ''", name="display_name_nonempty"),
        CheckConstraint(
            "EXTRACT(MINUTE FROM reminder_time) IN (0, 15, 30, 45) "
            "AND EXTRACT(SECOND FROM reminder_time) = 0",
            name="reminder_time_quarter_hour",
        ),
        CheckConstraint("mood_palette IN ('warm', 'green', 'calm')", name="mood_palette_values"),
        CheckConstraint("story_mode IN ('voice', 'text')", name="story_mode_values"),
        CheckConstraint("text_size IN ('normal', 'large', 'extra_large')", name="text_size_values"),
        CheckConstraint("revision >= 1", name="revision_positive"),
        {"schema": SCHEMA},
    )

    patient_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.patients.user_id"), primary_key=True
    )
    display_name: Mapped[str] = mapped_column(String(200))
    reminder_time: Mapped[time_of_day] = mapped_column(
        Time(), nullable=False, server_default=text("'21:00:00'"), default=time_of_day(21, 0)
    )
    mood_palette: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'warm'"), default="warm"
    )
    story_mode: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'voice'"), default="voice"
    )
    text_size: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'normal'"), default="normal"
    )
    onboarding_completed_at: Mapped[datetime | None] = timestamp(nullable=True)
    revision: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1"), default=1
    )
    updated_at: Mapped[datetime] = created_at()


class Diagnosis(Base):
    """Диагноз МКБ-10, поставленный врачом. Снятый диагноз не удаляется: `resolved_at`."""

    __tablename__ = "diagnoses"
    __table_args__ = (
        CheckConstraint("icd10 ~ '^[A-Z][0-9]{2}(\\.[0-9]{1,2})?$'", name="icd10_format"),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = uuid_pk()
    patient_id: Mapped[UUID] = mapped_column(ForeignKey(f"{SCHEMA}.patients.user_id"), index=True)
    icd10: Mapped[str] = mapped_column(String(8))
    title: Mapped[str] = mapped_column(String(200))
    set_by: Mapped[UUID] = mapped_column(ForeignKey(USERS))
    set_at: Mapped[datetime] = created_at()
    resolved_at: Mapped[datetime | None] = timestamp(nullable=True)
    resolved_by: Mapped[UUID | None] = mapped_column(ForeignKey(USERS))


class AccessGrant(Base):
    """Временный доступ заместителя к пациенту по сигналу (спека #56, раздел 3).

    Действует, пока `ended_at` пуст и `expires_at` в будущем. Закрывается решением сигнала
    (`resolved`), действием ведущего врача (`lead_took`) или отзывом назначения (`revoked`).
    `alert_id` — сигнал модуля alerts, без внешнего ключа: чужие таблицы не связываем.
    """

    __tablename__ = "access_grants"
    __table_args__ = (
        CheckConstraint("end_reason IN ('resolved', 'lead_took', 'revoked')", name="end_reason"),
        CheckConstraint("(ended_at IS NULL) = (end_reason IS NULL)", name="end_complete"),
        CheckConstraint("expires_at > granted_at", name="expires"),
        Index("ix_patients_access_grants_doctor_patient", "doctor_id", "patient_id"),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = uuid_pk()
    patient_id: Mapped[UUID] = mapped_column(ForeignKey(f"{SCHEMA}.patients.user_id"))
    doctor_id: Mapped[UUID] = mapped_column(ForeignKey(USERS))
    alert_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    granted_at: Mapped[datetime] = created_at()
    expires_at: Mapped[datetime] = timestamp()
    ended_at: Mapped[datetime | None] = timestamp(nullable=True)
    end_reason: Mapped[str | None] = mapped_column(String(16))
