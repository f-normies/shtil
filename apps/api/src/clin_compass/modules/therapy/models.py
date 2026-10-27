"""Таблицы модуля therapy (схема Postgres `therapy`)."""

from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from clin_compass.core.orm import Base, created_at, timestamp, uuid_pk

SCHEMA = "therapy"
USERS = "identity.users.id"


class Prescription(Base):
    """Назначение после подтверждения врачом. Отмена пишется полями `stopped_*`, строка остаётся.

    `card_snapshot` — данные справочника на момент назначения: показание, схема, указания,
    проверки и источники. Инструкция может обновиться, назначение остаётся объяснимым.
    """

    __tablename__ = "prescriptions"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[UUID] = uuid_pk()
    patient_id: Mapped[UUID] = mapped_column(ForeignKey(USERS), index=True)
    drug_id: Mapped[str] = mapped_column(String(64))
    indication_id: Mapped[str] = mapped_column(String(64))
    dose_mg: Mapped[float] = mapped_column(Float)
    times_per_day: Mapped[int] = mapped_column(Integer)
    schedule: Mapped[str] = mapped_column(String(200))
    duration_days: Mapped[int | None] = mapped_column(Integer)
    off_label: Mapped[bool] = mapped_column(Boolean)
    off_label_reason: Mapped[str | None] = mapped_column(Text)
    card_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    proposal_id: Mapped[UUID] = mapped_column(ForeignKey("proposals.proposals.id"))
    confirmed_by: Mapped[UUID] = mapped_column(ForeignKey(USERS))
    started_at: Mapped[datetime] = created_at()
    stopped_at: Mapped[datetime | None] = timestamp(nullable=True)
    stopped_by: Mapped[UUID | None] = mapped_column(ForeignKey(USERS))
    stop_reason: Mapped[str | None] = mapped_column(Text)
    stop_proposal_id: Mapped[UUID | None] = mapped_column(ForeignKey("proposals.proposals.id"))
    # Последний статус каждого правила безопасности (`contra:…`, `caution:…`): сигнал врачу
    # поднимается, только если статус стал хуже (спека #29, раздел 3).
    safety_state: Mapped[dict[str, str]] = mapped_column(JSONB, server_default="{}")


class PrescriptionAppearance(Base):
    """Персональная поправка вида активного назначения и её CAS-версия."""

    __tablename__ = "prescription_appearances"
    __table_args__ = (
        CheckConstraint(
            "(shape IS NULL AND color IS NULL) OR (shape IS NOT NULL AND color IS NOT NULL)",
            name="shape_and_color_together",
        ),
        CheckConstraint(
            "shape IS NULL OR shape IN ('round', 'oval', 'capsule')", name="shape_values"
        ),
        CheckConstraint(
            "color IS NULL OR color IN ('white', 'sand', 'peach', 'blue')", name="color_values"
        ),
        CheckConstraint("revision >= 1", name="revision_positive"),
        {"schema": SCHEMA},
    )

    prescription_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.prescriptions.id"), primary_key=True
    )
    shape: Mapped[str | None] = mapped_column(String(16))
    color: Mapped[str | None] = mapped_column(String(16))
    revision: Mapped[int] = mapped_column(Integer, server_default=text("1"))


class Medication(Base):
    """Препарат, который пациент принимает вне системы. Вносит врач (спека #29).

    `inn` — МНН из снимка ГРЛС или выбранное врачом; без него группы взаимодействий
    не проверяются, и подбор просит врача проверить сам. Отмена — `stopped_on`, строка остаётся.
    """

    __tablename__ = "medications"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[UUID] = uuid_pk()
    patient_id: Mapped[UUID] = mapped_column(ForeignKey(USERS), index=True)
    name: Mapped[str] = mapped_column(String(200))
    inn: Mapped[str | None] = mapped_column(String(200))
    registry_check: Mapped[str] = mapped_column(String(16))
    started_on: Mapped[date | None] = mapped_column(Date)
    stopped_on: Mapped[date | None] = mapped_column(Date)
    entered_by: Mapped[UUID] = mapped_column(ForeignKey(USERS))
    created_at: Mapped[datetime] = created_at()


class ReactionReport(Base):
    """Отметка пациентом нежелательной реакции на назначение (спека #29). Строки не удаляются.

    `reactions` — снимки реакций карточки: id, название, слова пациента и уровень сигнала.
    """

    __tablename__ = "reaction_reports"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[UUID] = uuid_pk()
    prescription_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.prescriptions.id"), index=True
    )
    patient_id: Mapped[UUID] = mapped_column(ForeignKey(USERS), index=True)
    reactions: Mapped[list[dict[str, str]]] = mapped_column(JSONB)
    note: Mapped[str | None] = mapped_column(Text)
    reported_at: Mapped[datetime] = created_at()


class Guidance(Base):
    """Справка по КР для пациента и ключа профиля (спека #107, 4.3). Строки не удаляются.

    `profile` — профиль без ФИО, контактов и даты рождения: по нему задача строит вопрос.
    `answer` — `Answer` модуля knowledge, пока ответа нет — null. Ответ с тем же ключом
    у другого пациента копируется вместе с `run_id` и `computed_at`.
    """

    __tablename__ = "guidance"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'done', 'failed')", name="status"),
        CheckConstraint("failures >= 0", name="failures"),
        CheckConstraint(
            "(status = 'done') = (answer IS NOT NULL AND computed_at IS NOT NULL)",
            name="done_has_answer",
        ),
        UniqueConstraint("patient_id", "profile_key"),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = uuid_pk()
    patient_id: Mapped[UUID] = mapped_column(ForeignKey(USERS))
    profile_key: Mapped[str] = mapped_column(String(64), index=True)
    profile: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(8))
    answer: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # Строка ai_runs вызова LLM. Без внешнего ключа: таблица ядра в другой схеме.
    run_id: Mapped[UUID | None]
    # Неудачные попытки задачи. Ожидание недоступного узла сюда не входит.
    failures: Mapped[int] = mapped_column(Integer, server_default="0")
    created_at: Mapped[datetime] = created_at()
    computed_at: Mapped[datetime | None] = timestamp(nullable=True)
