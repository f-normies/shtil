"""Решения врача по сигналам: `signal-decisions.yaml` (спека #93)."""

from typing import Annotated, Literal

from pydantic import Field

from clin_compass_knowledge.schemas.common import Id, Model, NonEmpty, SourceRef, Status

Sources = Annotated[tuple[SourceRef, ...], Field(min_length=1)]


class SafetyDecision(Model):
    """Решение, которое закрывает сигнал безопасности «Обязательно»."""

    id: Id
    text: NonEmpty
    reason_hint: NonEmpty  # что врач пишет в причине
    sources: Sources


class NoContact(Model):
    """Запись «Не удалось связаться»: пишется в историю, сигнал не закрывает."""

    text: NonEmpty
    sources: Sources


class Safety(Model):
    """Сигналы о суицидальном риске: правила `kind: alert` и виды признаков `signs/`."""

    decisions: tuple[SafetyDecision, ...] = Field(min_length=1)
    no_contact: NoContact


# Вид сигнала безопасности терапии (спека #95): правило `contra:`, правило `caution:` или
# препарат без МНН, отметка нежелательной реакции пациентом.
TherapySignalKind = Literal["contraindication", "caution", "reaction"]
THERAPY_SIGNAL_KINDS: tuple[TherapySignalKind, ...] = ("contraindication", "caution", "reaction")


class TherapyDecision(Model):
    """Решение по сигналу безопасности терапии (спека #95).

    `applies_to` — к каким видам сигнала вариант подходит. Организационный вариант может быть
    без источника, как причина отказа от направления.
    """

    id: Id
    text: NonEmpty
    reason_hint: NonEmpty
    applies_to: tuple[TherapySignalKind, ...] = Field(min_length=1)
    sources: tuple[SourceRef, ...] = ()


class Therapy(Model):
    """Сигналы безопасности терапии: противопоказание, мера предосторожности, реакция (#29)."""

    decisions: tuple[TherapyDecision, ...] = Field(min_length=1)


class DeclineReason(Model):
    """Причина отказа от направления. Организационная причина может быть без источника."""

    id: Id
    text: NonEmpty
    sources: tuple[SourceRef, ...] = ()


class ReferralDecline(Model):
    """Причины отказа от направления к психиатру; комментарий врача обязателен всегда."""

    reasons: tuple[DeclineReason, ...] = Field(min_length=1)


class Snooze(Model):
    """Отсрочка сигнала «Желательно»: предел и готовые варианты в днях."""

    max_days: int = Field(ge=1)
    presets_days: tuple[Annotated[int, Field(ge=1)], ...] = Field(min_length=1)
    basis: NonEmpty  # обоснование предела: норматива в КР нет


class SignalDecisions(Model):
    """Файл `signal-decisions.yaml`."""

    version: int = Field(ge=1)
    status: Status
    safety: Safety
    therapy: Therapy
    referral_decline: ReferralDecline
    snooze: Snooze
