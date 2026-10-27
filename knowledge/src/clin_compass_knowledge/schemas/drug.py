"""Препарат справочника `drugs/<id>.yaml` (спека #28, раздел 4).

Данные — по инструкции (ОХЛП): показания с кодами МКБ-10, возраст, схема по показанию,
указания по приёму. У каждого поля — ссылка на раздел инструкции. Коды МКБ-10 показаний
сопоставляет медик: в инструкции их нет.
"""

from datetime import date
from typing import Annotated, Any, Literal, Self

from pydantic import Field, PositiveFloat, PositiveInt, model_validator

from clin_compass_knowledge.schemas.common import Id, Model, NonEmpty, SourceRef, Status

Icd10 = Annotated[str, Field(pattern=r"^[A-Z][0-9]{2}(\.[0-9]{1,2})?$")]
_CODE = r"[A-Z][0-9]{2}(\.[0-9]{1,2})?"
# Код, рубрика или диапазон: «G40», «E74.3», «F00-F09», «K74.3-K74.6».
Icd10Pattern = Annotated[str, Field(pattern=rf"^{_CODE}(-{_CODE})?$")]
FactName = Literal["egfr", "child_pugh", "pregnant", "breastfeeding"]
FactOp = Literal["<", "<=", ">", ">=", "=="]
Outcome = Literal["yes", "no", "unknown"]


class FormAppearance(Model):
    """Свойства внешнего вида лекарственной формы, подтверждённые инструкцией."""

    shape: Literal["round", "oval", "capsule"] | None
    color: Literal["white", "sand", "peach", "blue"] | None
    description: NonEmpty
    source: SourceRef


class Form(Model):
    """Лекарственная форма и дозировка."""

    id: Id
    title: NonEmpty
    strength_mg: PositiveFloat
    appearance: FormAppearance | None = None


class AgeMin(Model):
    years: PositiveInt
    source: SourceRef


class Duration(Model):
    """Длительность приёма. Без `days` — длительность определяет врач."""

    text: NonEmpty
    days: PositiveInt | None = None
    max_days: PositiveInt | None = None

    @model_validator(mode="after")
    def _max_not_below_days(self) -> Self:
        if self.max_days is not None and (self.days is None or self.max_days < self.days):
            raise ValueError("max_days задаётся вместе с days и не меньше его")
        return self


class Regimen(Model):
    """Схема по инструкции: разовая доза, кратность, расписание, длительность."""

    form: Id
    dose_mg: PositiveFloat
    times_per_day: PositiveInt
    schedule: NonEmpty
    duration: Duration
    source: SourceRef


class Indication(Model):
    """Показание: подходит, если у пациента есть диагноз из `icd10` и, при `requires_any`,
    ещё один из этих кодов. `manual` — условия, которые подтверждает врач."""

    id: Id
    title: NonEmpty
    icd10: tuple[Icd10, ...] = Field(min_length=1)
    requires_any: tuple[Icd10, ...] = ()
    manual: tuple[NonEmpty, ...] = ()
    source: SourceRef
    regimen: Regimen


class Instruction(Model):
    """Указание по приёму для врача и пациента."""

    text: NonEmpty
    source: SourceRef


class Registration(Model):
    """Номер регистрационного удостоверения и снимок реестра, где он найден."""

    number: NonEmpty
    source: SourceRef


class Drug(Model):
    """Препарат, зарегистрированный в РФ, с данными инструкции."""

    id: Id
    version: PositiveInt
    status: Status
    trade_name: NonEmpty
    inn: NonEmpty
    registration: Registration
    label: Id
    # ▼ «Подлежит дополнительному мониторингу»: пометка попадает в черновик извещения.
    additional_monitoring: bool = False
    forms: tuple[Form, ...] = Field(min_length=1)
    age_min: AgeMin
    indications: tuple[Indication, ...] = Field(min_length=1)
    instructions: tuple[Instruction, ...] = ()
    contraindications: tuple["SafetyRule", ...] = ()
    cautions: tuple["SafetyRule", ...] = ()
    adverse_reactions: tuple["AdverseReaction", ...] = ()
    reaction_actions: "ReactionActions | None" = None


class FactCondition(Model):
    """Значение профиля: сравнение (`op` и `value`) или вхождение в список (`in`)."""

    name: FactName
    op: FactOp | None = None
    value: float | bool | str | None = None
    in_: tuple[str, ...] | None = Field(default=None, alias="in")

    @model_validator(mode="after")
    def _one_form(self) -> Self:
        compare = self.op is not None and self.value is not None
        if compare == (self.in_ is not None):
            raise ValueError("нужно ровно одно из: op и value или in")
        return self


class MedicationCondition(Model):
    """Препарат группы: принимает сейчас или отменил меньше `within_days` дней назад."""

    group: Id
    within_days: PositiveInt | None = None


class SafetyCondition(Model):
    """Ровно одно из: диагноз, значение профиля, препарат группы, вопрос врачу."""

    diagnosis: tuple[Icd10Pattern, ...] | None = Field(default=None, min_length=1)
    fact: FactCondition | None = None
    medication: MedicationCondition | None = None
    ask: NonEmpty | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> Self:
        if sum(v is not None for v in (self.diagnosis, self.fact, self.medication, self.ask)) != 1:
            raise ValueError("условие — ровно одно из: diagnosis, fact, medication, ask")
        return self

    @model_validator(mode="after")
    def _ranges_in_order(self) -> Self:
        """Границы диапазона одной точности («F00-F09», «K74.3-K74.6»), начало не больше конца."""
        for pattern in self.diagnosis or ():
            low, _, high = pattern.partition("-")
            if high and (len(low) != len(high) or low > high):
                raise ValueError(f"диапазон {pattern}: границы разной точности или не по порядку")
        return self


class SafetyWhen(Model):
    any: tuple[SafetyCondition, ...] = Field(min_length=1)


class CaseMedication(Model):
    inn: NonEmpty
    stopped_days_ago: int | None = Field(default=None, ge=0)


class CaseGiven(Model):
    """Данные пациента в табличном случае. Сегодня — фиксированная дата проверки."""

    sex: Literal["female", "male"] | None = None
    diagnoses: tuple[Icd10, ...] = ()
    facts: dict[FactName, Any] = Field(default_factory=dict)
    medications: tuple[CaseMedication, ...] = ()


class SafetyCase(Model):
    given: CaseGiven
    result: Outcome
    source: SourceRef


class SafetyRule(Model):
    """Противопоказание или мера предосторожности с источником и табличными случаями."""

    id: Id
    title: NonEmpty
    when: SafetyWhen
    doctor_actions: tuple[NonEmpty, ...] = Field(min_length=1)
    sources: tuple[SourceRef, ...] = Field(min_length=1)
    cases: tuple[SafetyCase, ...] = Field(min_length=1)


class AdverseReaction(Model):
    """Нежелательная реакция из инструкции. `level` — уровень сигнала, если её отметил пациент."""

    id: Id
    title: NonEmpty
    frequency: NonEmpty
    level: Literal["mandatory", "desirable"]
    patient_reportable: bool
    patient_title: NonEmpty | None = None
    sources: tuple[SourceRef, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _words_for_patient(self) -> Self:
        if self.patient_reportable and self.patient_title is None:
            raise ValueError("реакции, которую отмечает пациент, нужен patient_title")
        return self


class ReactionActions(Model):
    """Действия врача по сигналу, который поднимает отметка реакции пациентом (спека #29)."""

    doctor_actions: tuple[NonEmpty, ...] = Field(min_length=1)
    sources: tuple[SourceRef, ...] = Field(min_length=1)


# Дата, от которой считаются `stopped_days_ago` в табличных случаях.
CASE_TODAY = date(2026, 10, 18)
