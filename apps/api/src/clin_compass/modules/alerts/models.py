"""Таблицы модуля alerts (схема Postgres `alerts`)."""

from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    SmallInteger,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from clin_compass.core.orm import Base, created_at, timestamp, uuid_pk

SCHEMA = "alerts"
USERS = "identity.users.id"
LEVELS = "level IN ('mandatory', 'desirable')"
# Один нерешённый сигнал на правило у пациента: повторы дописываются к нему (спека #95).
OPEN_RULE_ALERT = text("state <> 'resolved' AND rule_id IS NOT NULL")
STATES = "state IN ('new', 'in_work', 'snoozed', 'resolved')"
ACTIONS = "action IN ('acknowledge', 'snooze', 'unsnooze', 'no_contact', 'resolve', 'escalate')"


class Alert(Base):
    """Сигнал врачу. Снимок правила на момент создания; срабатывания — в `triggers`.

    `state` — состояние для врача (спека #95): `new`, `in_work`, `snoozed` до `snoozed_until`,
    `resolved`. `acknowledged_at` — первое взятие в работу. Действия врача — в `actions`.
    `urgent_since` — когда сигнал стал «Обязательно», от него идут шаги эскалации (спека #56);
    у сигналов до эскалации пуст. `deputy_id` — заместитель, которому передан сигнал.
    Сигнал не удаляется: DELETE и TRUNCATE запрещает триггер в БД (миграция 8309abae9e22).
    """

    __tablename__ = "alerts"
    __table_args__ = (
        CheckConstraint("origin_type IN ('rule', 'ai_run')", name="origin_type"),
        CheckConstraint("kind IN ('alert', 'routing', 'safety')", name="kind"),
        CheckConstraint(LEVELS, name="level"),
        CheckConstraint("delivered_via IN ('ws', 'rest')", name="delivered_via"),
        CheckConstraint("occurrences >= 1", name="occurrences"),
        CheckConstraint("jsonb_array_length(doctor_actions) >= 1", name="doctor_actions"),
        CheckConstraint(
            "(delivered_at IS NULL) = (delivered_via IS NULL)", name="delivery_complete"
        ),
        CheckConstraint(
            "(acknowledged_at IS NULL) = (acknowledged_by IS NULL)", name="acknowledge_complete"
        ),
        # У правила есть и id, и версия; у сигнала из AI-контура нет ни того, ни другого.
        CheckConstraint(
            "(origin_type = 'rule') = (rule_id IS NOT NULL) "
            "AND (rule_id IS NULL) = (rule_version IS NULL)",
            name="rule_matches_origin",
        ),
        # У правила всегда есть клинические документы; у сигнала из AI-контура их нет.
        CheckConstraint(
            "(origin_type = 'rule') = (jsonb_array_length(sources) >= 1)",
            name="sources_match_origin",
        ),
        CheckConstraint("origin_type = 'rule' OR kind = 'alert'", name="ai_kind"),
        CheckConstraint(STATES, name="state"),
        CheckConstraint("(state = 'snoozed') = (snoozed_until IS NOT NULL)", name="snooze_date"),
        CheckConstraint(
            "(state = 'resolved') = (resolved_at IS NOT NULL) "
            "AND (resolved_at IS NULL) = (resolved_by IS NULL)",
            name="resolve_complete",
        ),
        Index(
            "uq_alerts_open_rule",
            "patient_id",
            "rule_id",
            unique=True,
            postgresql_where=OPEN_RULE_ALERT,
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = uuid_pk()
    patient_id: Mapped[UUID] = mapped_column(ForeignKey(USERS), index=True)
    doctor_id: Mapped[UUID] = mapped_column(ForeignKey(USERS), index=True)
    origin_type: Mapped[str] = mapped_column(String(16))
    rule_id: Mapped[str | None] = mapped_column(String(200))
    rule_version: Mapped[int | None]
    kind: Mapped[str] = mapped_column(String(16))
    level: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(Text)
    doctor_actions: Mapped[list[str]] = mapped_column(JSONB)
    sources: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    occurrences: Mapped[int] = mapped_column(server_default="1")
    created_at: Mapped[datetime] = created_at()
    # Не через created_at(): внутри класса это имя уже занято колонкой выше.
    last_triggered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    delivered_at: Mapped[datetime | None] = timestamp(nullable=True)
    delivered_via: Mapped[str | None] = mapped_column(String(8))
    acknowledged_at: Mapped[datetime | None] = timestamp(nullable=True)
    acknowledged_by: Mapped[UUID | None] = mapped_column(ForeignKey(USERS))
    state: Mapped[str] = mapped_column(String(16), server_default="new")
    snoozed_until: Mapped[date | None] = mapped_column(Date)
    resolved_at: Mapped[datetime | None] = timestamp(nullable=True)
    resolved_by: Mapped[UUID | None] = mapped_column(ForeignKey(USERS))
    urgent_since: Mapped[datetime | None] = timestamp(nullable=True)
    deputy_id: Mapped[UUID | None] = mapped_column(ForeignKey(USERS))

    triggers: Mapped[list["Trigger"]] = relationship(order_by="Trigger.id", lazy="raise")
    actions: Mapped[list["Action"]] = relationship(order_by="Action.id", lazy="raise")


class Trigger(Base):
    """Срабатывание сигнала. Только дописывается: это медицинские данные.

    UPDATE, DELETE и TRUNCATE запрещает триггер в БД (миграция 8309abae9e22).
    """

    __tablename__ = "triggers"
    __table_args__ = (
        CheckConstraint(LEVELS, name="level"),
        CheckConstraint("origin_ref <> ''", name="origin_ref"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    alert_id: Mapped[UUID] = mapped_column(ForeignKey(f"{SCHEMA}.alerts.id"), index=True)
    at: Mapped[datetime] = created_at()
    origin_ref: Mapped[str] = mapped_column(String(200))
    rule_version: Mapped[int | None]
    level: Mapped[str] = mapped_column(String(16))
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB)


class Action(Base):
    """Действие врача или системы по сигналу (спека #95). Только дописывается.

    `actor_id` пуст у действия системы (снятие отсрочки подъёмом уровня, эскалация).
    `decision_text` — текст варианта на момент решения: правка `knowledge/` историю не меняет.
    У `escalate` — `step` (1–3) и `recipient_id`: врач, заместитель или пусто, если
    заместителя нет или сообщено администраторам (спека #56).
    UPDATE, DELETE и TRUNCATE запрещает триггер в БД.
    """

    __tablename__ = "actions"
    __table_args__ = (
        CheckConstraint(ACTIONS, name="action"),
        CheckConstraint("(action = 'escalate') = (step IS NOT NULL)", name="escalate_step"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    alert_id: Mapped[UUID] = mapped_column(ForeignKey(f"{SCHEMA}.alerts.id"), index=True)
    at: Mapped[datetime] = created_at()
    actor_id: Mapped[UUID | None] = mapped_column(ForeignKey(USERS))
    action: Mapped[str] = mapped_column(String(16))
    decision_id: Mapped[str | None] = mapped_column(String(64))
    decision_text: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    snoozed_until: Mapped[date | None] = mapped_column(Date)
    step: Mapped[int | None] = mapped_column(SmallInteger)
    recipient_id: Mapped[UUID | None] = mapped_column(ForeignKey(USERS))


class Deputy(Base):
    """Назначение заместителя врача администратором (спека #56, раздел 3).

    Обе даты входят в период. Строка не удаляется: отзыв ставит `revoked_at` и `revoked_by`.
    Пересечение действующих периодов у врача проверяет сервис под блокировкой.
    """

    __tablename__ = "deputies"
    __table_args__ = (
        CheckConstraint("doctor_id <> deputy_id", name="not_self"),
        CheckConstraint("starts_on <= ends_on", name="period"),
        CheckConstraint("ends_on - starts_on < 92", name="period_length"),
        CheckConstraint("btrim(basis) <> ''", name="basis"),
        CheckConstraint("(revoked_at IS NULL) = (revoked_by IS NULL)", name="revoke_complete"),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = uuid_pk()
    doctor_id: Mapped[UUID] = mapped_column(ForeignKey(USERS), index=True)
    deputy_id: Mapped[UUID] = mapped_column(ForeignKey(USERS), index=True)
    starts_on: Mapped[date] = mapped_column(Date)
    ends_on: Mapped[date] = mapped_column(Date)
    basis: Mapped[str] = mapped_column(Text)
    assigned_by: Mapped[UUID] = mapped_column(ForeignKey(USERS))
    created_at: Mapped[datetime] = created_at()
    revoked_at: Mapped[datetime | None] = timestamp(nullable=True)
    revoked_by: Mapped[UUID | None] = mapped_column(ForeignKey(USERS))


class Review(Base):
    """Администратор разобрал эскалацию сигнала (спека #56): одна запись на сигнал."""

    __tablename__ = "reviews"
    __table_args__ = {"schema": SCHEMA}

    alert_id: Mapped[UUID] = mapped_column(ForeignKey(f"{SCHEMA}.alerts.id"), primary_key=True)
    reviewed_by: Mapped[UUID] = mapped_column(ForeignKey(USERS))
    reviewed_at: Mapped[datetime] = created_at()
