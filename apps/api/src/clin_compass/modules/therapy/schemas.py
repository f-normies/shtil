"""Публичные схемы модуля therapy."""

import datetime as dt
from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PositiveFloat,
    PositiveInt,
    field_validator,
    model_validator,
)

from clin_compass.modules.knowledge.schemas import Claim, RegistryInfo
from clin_compass.modules.proposals.schemas import ProposalOut

CheckStatus = Literal["pass", "fail", "warn", "manual"]
PrescriptionStatus = Literal["active", "completed", "stopped"]
GuidanceStatus = Literal["pending", "done", "failed"]
# Возрастная группа профиля справки по КР (спека #107, 4.2).
AgeGroup = Literal["<18", "18–64", "≥65"]


class SourceLink(BaseModel):
    """Место в документе и сам документ из манифеста.

    `version` и `date` — версия и дата документа (спека #28, 8.7). В снимках до них — None.
    """

    source: str
    locator: str
    title: str
    url: str
    version: str | None = None
    date: dt.date | None = None


PillShape = Literal["round", "oval", "capsule"]
PillColor = Literal["white", "sand", "peach", "blue"]
AppearanceOrigin = Literal["instruction", "patient", "unknown"]


class PillAppearance(BaseModel):
    """Распознанные свойства значка таблетки; неизвестные значения остаются null."""

    shape: PillShape | None
    color: PillColor | None


class SourcedPillAppearance(PillAppearance):
    description: str
    source: SourceLink


class PillAppearanceChoice(BaseModel):
    """Полный вид, выбранный пациентом."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    shape: PillShape
    color: PillColor


class AppearanceState(BaseModel):
    """Эффективный вид назначения и подтверждённое инструкцией значение по умолчанию."""

    default_appearance: SourcedPillAppearance | None
    appearance: PillAppearance | None
    appearance_origin: AppearanceOrigin
    appearance_revision: int = Field(ge=0)


class AppearanceUpdateIn(BaseModel):
    """Персональная поправка внешнего вида; null сбрасывает её к инструкции."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    expected_revision: int = Field(ge=0)
    appearance: PillAppearanceChoice | None


class CheckOut(BaseModel):
    id: str
    title: str
    status: CheckStatus
    detail: str
    source: SourceLink | None
    actions: list[str] = []


class LabelRegimen(BaseModel):
    """Схема по инструкции для показания."""

    form: str
    # Крепость формы: из неё и дозы экран считает число таблеток. В снимках до #28 v4 нет.
    strength_mg: float | None = None
    dose_mg: float
    times_per_day: int
    schedule: str
    duration_days: int | None
    max_days: int | None
    duration_text: str
    source: SourceLink


class InstructionOut(BaseModel):
    text: str
    source: SourceLink


class OptionOut(BaseModel):
    """Препарат справочника по одному показанию: проверки и, если можно, предложение."""

    drug_id: str
    trade_name: str
    inn: str
    indication_id: str
    indication_title: str
    indication_icd10: list[str]
    regimen: LabelRegimen
    instructions: list[InstructionOut]
    checks: list[CheckOut]
    # Номер РУ, статус и дата снимка ГРЛС; None — номера нет в снимке.
    registration: RegistryInfo | None = None
    proposal: ProposalOut | None


class OptionsOut(BaseModel):
    options: list[OptionOut]
    message: str | None


class RegimenIn(BaseModel):
    """Схема, выбранная врачом. `duration_days: None` — без срока."""

    model_config = ConfigDict(frozen=True)

    dose_mg: PositiveFloat = Field(le=10_000)
    times_per_day: PositiveInt = Field(le=12)
    duration_days: PositiveInt | None = Field(le=3650)
    # Расписание врача. Без него при той же кратности остаётся расписание инструкции.
    schedule: str | None = Field(default=None, min_length=1, max_length=200)


class ReviseIn(BaseModel):
    """Подтверждение с правкой схемы. Вне инструкции `comment` — обоснование, от 10 символов."""

    regimen: RegimenIn
    comment: str | None = Field(default=None, max_length=2000)


class StopIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)

    @field_validator("reason")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 3:
            raise ValueError("reason must have at least 3 non-space characters")
        return value


class PrescriptionOut(BaseModel):
    id: UUID
    patient_id: UUID
    drug_id: str
    indication_id: str
    trade_name: str
    inn: str
    dose_mg: float
    times_per_day: int
    schedule: str
    duration_days: int | None
    ends_on: date | None
    status: PrescriptionStatus
    off_label: bool
    off_label_reason: str | None
    card_snapshot: dict[str, Any]
    proposal_id: UUID
    confirmed_by: UUID
    started_at: datetime
    stopped_at: datetime | None
    stopped_by: UUID | None
    stop_reason: str | None


class ReactionOption(BaseModel):
    """Нежелательная реакция, которую пациент может отметить: `title` — его словами."""

    id: str
    title: str


class ScheduleItem(AppearanceState):
    """Строка схемы приёма для пациента: без проверок и служебных полей.

    `reactions` — перечень реакций из инструкции для отметки (спека #29).
    """

    prescription_id: UUID
    trade_name: str
    inn: str
    dose_mg: float
    times_per_day: int
    schedule: str
    started_at: datetime
    ends_on: date | None
    instructions: list[str]
    reactions: list[ReactionOption] = []


class ReactionReportIn(BaseModel):
    """Отметка пациента: реакции из перечня назначения и, если хочет, свой текст."""

    reactions: list[str] = Field(min_length=1, max_length=50)
    note: str | None = Field(default=None, max_length=2000)

    @field_validator("note")
    @classmethod
    def _blank_is_none(cls, value: str | None) -> str | None:
        value = value.strip() if value else None
        return value or None


class ReportedReaction(BaseModel):
    """Отмеченная реакция: снимок карточки на момент отметки."""

    id: str
    title: str
    patient_title: str
    level: Literal["mandatory", "desirable"]


class ReactionReportOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    prescription_id: UUID
    reactions: list[ReportedReaction]
    note: str | None
    reported_at: datetime


class AdverseEventDraftOut(BaseModel):
    """Черновик извещения о нежелательной реакции. Отправляет врач вне системы."""

    prescription_id: UUID
    patient_id: UUID
    text: str


class IntakeSchemeSnapshot(BaseModel):
    """Минимальная схема назначения, проверенная на календарную дату дозы."""

    model_config = ConfigDict(frozen=True)

    prescription_id: UUID
    trade_name: str
    inn: str
    dose_mg: float
    times_per_day: int
    schedule: str
    started_on: date
    ends_on: date | None
    stopped_on: date | None


class MedicationIn(BaseModel):
    """Препарат вне системы. `inn` — если врач выбрал МНН из похожих названий."""

    name: str = Field(min_length=1, max_length=200)
    inn: str | None = Field(default=None, min_length=1, max_length=200)
    started_on: date | None = None
    stopped_on: date | None = None

    @model_validator(mode="after")
    def _stop_not_before_start(self) -> "MedicationIn":
        if self.started_on and self.stopped_on and self.stopped_on < self.started_on:
            raise ValueError("stopped_on must not be before started_on")
        return self


class MedicationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    inn: str | None
    registry_check: str
    started_on: date | None
    stopped_on: date | None
    entered_by: UUID
    created_at: datetime


class MedicationStopIn(BaseModel):
    stopped_on: date


class DrugMatchOut(BaseModel):
    trade_name: str | None
    inn: str
    reg_number: str | None


class DrugLookupOut(BaseModel):
    """Сверка названия со снимком ГРЛС (`registered`, `inactive`, `similar`, `not_found`…).

    `inn` — МНН точного совпадения, если он один у всех удостоверений. Иначе МНН выбирает врач
    из `inn_options`: все разные МНН названия или МНН похожих названий, без повторов.
    """

    check: str
    matches: list[DrugMatchOut]
    inn: str | None = None
    inn_options: list[str] = []


class GuidelineOut(BaseModel):
    """Клиническая рекомендация профиля: id и название из манифеста."""

    id: str
    title: str


class GuidanceProfileOut(BaseModel):
    """Профиль, по которому считался ответ справки: коды диагнозов, пол и возрастная группа."""

    diagnoses: list[str]
    sex: str | None
    age_group: AgeGroup | None


class GuidanceOut(BaseModel):
    """Варианты по КР в разделе «Терапия» (спека #107, 4.5). Это справка, не предложение.

    `status` — расчёт по текущему профилю; None — у пациента нет активных диагнозов.
    `stale` — показан ответ по прежнему профилю, а текущий ещё считается или упал.
    `claims`, `computed_at` и `profile` — показанного ответа: при `stale` профиль прежний,
    без ответа — None. По профилю инспектор источника показывает применимость (спека #106).
    `guidelines` пуст — КР по диагнозам нет.
    `catalog_drugs` — торговые названия справочника: назначить можно только их.
    """

    status: GuidanceStatus | None
    stale: bool
    claims: list[Claim]
    computed_at: datetime | None
    guidelines: list[GuidelineOut]
    catalog_drugs: list[str]
    profile: GuidanceProfileOut | None
