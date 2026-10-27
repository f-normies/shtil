"""Публичные схемы модуля alerts."""

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from clin_compass_knowledge.schemas.rule import Condition
from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

from clin_compass.modules.patients.schemas import AccessGrantOut
from clin_compass.modules.proposals.schemas import ProposalOut

AlertKind = Literal["alert", "routing", "safety"]
AlertLevel = Literal["mandatory", "desirable"]
AlertOrigin = Literal["rule", "ai_run"]
AlertStatus = Literal["created", "delivered", "acknowledged"]
DeliveryChannel = Literal["ws", "rest"]
AlertState = Literal["new", "in_work", "snoozed", "resolved"]
ActionKind = Literal["acknowledge", "snooze", "unsnooze", "no_contact", "resolve", "escalate"]
EscalationStep = Literal[1, 2, 3]


class SourceSnapshot(BaseModel):
    """Ссылка на клинический документ, скопированная в сигнал при его создании."""

    model_config = ConfigDict(frozen=True)

    source: str
    locator: str
    title: str
    url: str


class TriggerOut(BaseModel):
    """Одно срабатывание: когда, по каким данным и какого уровня требовало."""

    model_config = ConfigDict(from_attributes=True, frozen=True)

    at: datetime
    origin_ref: str
    rule_version: int | None
    level: AlertLevel
    evidence: dict[str, Any]


class ActionOut(BaseModel):
    """Действие по сигналу (спека #95): кто, когда, что, вариант и причина. `actor_id` пуст
    у действия системы. У `escalate` (спека #56) — `step` и `recipient_id`: ведущий врач
    на шаге 1, заместитель на шаге 2 (пусто — заместитель не назначен), пусто на шаге 3."""

    model_config = ConfigDict(from_attributes=True, frozen=True)

    at: datetime
    actor_id: UUID | None
    action: ActionKind
    decision_id: str | None
    decision_text: str | None
    reason: str | None
    snoozed_until: date | None
    step: EscalationStep | None = None
    recipient_id: UUID | None = None


class AlertOut(BaseModel):
    """Сигнал врачу: снимок правила, ссылки на документы, история срабатываний и статус.

    У сигнала по правилу заполнены `rule_id`, `rule_version` и `sources`. У сигнала из
    AI-контура (`origin_type = ai_run`) их нет. `status` — ход доставки по отметкам времени:
    `created`, затем `delivered`, затем `acknowledged`. `state` — состояние для врача на
    сегодня (спека #95): отсрочка, дата которой наступила, отдаётся как `new`. `actors` —
    ФИО врачей из истории по id, с ведущим врачом и заместителем.

    Эскалация (спека #56): `urgent_since` — когда сигнал стал «Обязательно», `deputy_id` —
    заместитель шага 2, `deputy_access` — его доступ к пациенту, `as_deputy` — запрашивающий
    видит сигнал как заместитель.
    """

    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    patient_id: UUID
    doctor_id: UUID
    origin_type: AlertOrigin
    rule_id: str | None
    rule_version: int | None
    kind: AlertKind
    level: AlertLevel
    title: str
    doctor_actions: list[str]
    sources: list[SourceSnapshot]
    occurrences: int
    created_at: datetime
    last_triggered_at: datetime
    delivered_at: datetime | None
    delivered_via: DeliveryChannel | None
    acknowledged_at: datetime | None
    acknowledged_by: UUID | None
    state: AlertState
    snoozed_until: date | None
    resolved_at: datetime | None
    resolved_by: UUID | None
    triggers: list[TriggerOut]
    actions: list[ActionOut]
    actors: dict[UUID, str] = {}
    urgent_since: datetime | None = None
    deputy_id: UUID | None = None
    deputy_access: AccessGrantOut | None = None
    as_deputy: bool = False

    @computed_field  # type: ignore[prop-decorator]
    @property
    def escalation_step(self) -> EscalationStep | None:
        """Последний пройденный шаг эскалации."""
        return max((a.step for a in self.actions if a.step is not None), default=None)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def decision(self) -> ActionOut | None:
        """Решение по сигналу: последняя запись `resolve`."""
        return next((a for a in reversed(self.actions) if a.action == "resolve"), None)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def status(self) -> AlertStatus:
        if self.acknowledged_at is not None:
            return "acknowledged"
        if self.delivered_at is not None:
            return "delivered"
        return "created"


class SnoozeIn(BaseModel):
    """Отложить «Желательно» до даты: с завтра и не дальше предела из данных."""

    until: date


class ResolveIn(BaseModel):
    """Решение: вариант из `GET /alerts/decisions` и причина (у «Обязательно» обязательна)."""

    decision_id: str = Field(min_length=1, max_length=64)
    reason: str | None = Field(default=None, max_length=2000)


class DecisionOptionOut(BaseModel):
    id: str
    text: str
    reason_hint: str
    applies_to: list[str] = []


class ReasonOut(BaseModel):
    """Причина отказа от направления к психиатру (#93)."""

    id: str
    text: str


class SnoozeOptionsOut(BaseModel):
    max_days: int
    presets_days: list[int]


class DecisionsOut(BaseModel):
    """Варианты решений по видам сигнала и пределы отсрочки. Без данных — пустые списки."""

    safety: list[DecisionOptionOut]
    no_contact: str | None
    therapy: list[DecisionOptionOut]
    referral_decline: list[ReasonOut]
    snooze: SnoozeOptionsOut | None


class ThresholdOut(BaseModel):
    """Порог правила по шкале: для линейки и шкалы ответа в блоке «Причина»."""

    rule_id: str
    kind: str
    level: str
    item: str | None
    score: str | None
    op: str
    value: float


class HelpContactOut(BaseModel):
    """Номер на экране помощи; `primary` — главная кнопка звонка (спека #57)."""

    model_config = ConfigDict(frozen=True)

    id: str
    number: str
    label: str
    primary: bool
    sources: list[SourceSnapshot]


class HelpTriggerOut(BaseModel):
    """Условие правила `kind: alert`: PWA проверяет по нему ответы и без сети (спека #57)."""

    model_config = ConfigDict(frozen=True)

    rule_id: str
    rule_version: int
    when: Condition


class PatientHelpOut(BaseModel):
    """Экран помощи пациенту: текст, номера, дата проверки и условия открытия (спека #57)."""

    model_config = ConfigDict(frozen=True)

    version: int
    checked_on: date
    title: str
    text: str
    sources: list[SourceSnapshot]
    contacts: list[HelpContactOut]
    triggers: list[HelpTriggerOut]


class ReferralIn(BaseModel):
    """Решение по направлению к психиатру (спека #35). `alert_ids` — сигналы маршрутизации,
    которые врач видел в листе. `comment` — комментарий для психиатра или к отказу."""

    decision: Literal["confirm", "decline", "snooze"]
    alert_ids: list[UUID] = Field(min_length=1, max_length=20)
    reason_id: str | None = Field(default=None, max_length=64)
    comment: str | None = Field(default=None, max_length=2000)
    until: date | None = None


class ReferralOut(BaseModel):
    """Предложение направления после решения (у отсрочки — None) и закрытые сигналы."""

    proposal: ProposalOut | None
    alerts: list[AlertOut]


class DeputyIn(BaseModel):
    """Назначение заместителя (спека #56): обе даты входят в период, основание обязательно."""

    doctor_id: UUID
    deputy_id: UUID
    starts_on: date
    ends_on: date
    basis: str = Field(min_length=1, max_length=500)

    @field_validator("basis")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("basis must not be blank")
        return value.strip()


class DeputyOut(BaseModel):
    id: UUID
    doctor_id: UUID
    doctor_name: str
    deputy_id: UUID
    deputy_name: str
    starts_on: date
    ends_on: date
    basis: str
    created_at: datetime
    revoked_at: datetime | None


class AdminDoctorOut(BaseModel):
    """Врач организации и его заместитель на сегодня; `no_deputy` — заместителя нет."""

    id: UUID
    full_name: str
    deputy: DeputyOut | None
    no_deputy: bool


class EscalationStepOut(BaseModel):
    step: EscalationStep
    at: datetime
    recipient_id: UUID | None


class EscalationOut(BaseModel):
    """Эскалация для разбора администратором (спека #56, раздел 5). Без данных пациента:
    ни ФИО, ни заголовка, ни уровня сигнала. `number` — первые 8 знаков id сигнала."""

    alert_id: UUID
    number: str
    urgent_since: datetime | None
    doctor_id: UUID
    deputy_id: UUID | None
    step: EscalationStep
    steps: list[EscalationStepOut]
    acknowledged_at: datetime | None
    acknowledged_by: UUID | None
    resolved_at: datetime | None
    access: AccessGrantOut | None
    reviewed_at: datetime | None
    reviewed_by: UUID | None
    names: dict[UUID, str]
