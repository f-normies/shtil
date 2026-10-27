"""Публичные схемы модуля patients."""

import re
from datetime import date, datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

# E.164: «+», затем от 7 до 15 цифр ASCII, первая не 0.
# `\d` пропустил бы цифры других письменностей, а `tel:` их не набирает.
_E164 = re.compile(r"\+[1-9][0-9]{6,14}")
_SEPARATORS = re.compile(r"[\s()-]")
_REMINDER_TIME = re.compile(r"(?:[01][0-9]|2[0-3]):(?:00|15|30|45)")


class InviteOut(BaseModel):
    token: str
    expires_at: datetime


class ConsentDocument(BaseModel):
    """Неизменяемый текст согласия и его версия для регистрации."""

    model_config = ConfigDict(frozen=True)

    # Те же пределы, что у `RegistrationRequest.consent_version` и колонки `String(32)`.
    version: str = Field(min_length=1, max_length=32)
    text: str


class RegistrationRequest(BaseModel):
    invite_token: str
    email: EmailStr
    password: str = Field(min_length=8)
    full_name: str = Field(min_length=1, max_length=200)
    # Без согласия на обработку персональных данных регистрация невозможна.
    consent_personal_data: Literal[True]
    consent_version: str = Field(min_length=1, max_length=32)


Sex = Literal["female", "male"]
# Печёночная недостаточность по Чайлд-Пью; `none` — её нет.
ChildPugh = Literal["none", "A", "B", "C"]
Icd10 = Annotated[str, Field(pattern=r"^[A-Z][0-9]{2}(\.[0-9]{1,2})?$")]


PatientAccess = Literal["lead", "deputy"]
AccessEndReason = Literal["resolved", "lead_took", "revoked"]


class AccessGrantOut(BaseModel):
    """Временный доступ заместителя по сигналу (спека #56). `ended_at` пуст — не закрыт;
    истечение видно по `expires_at`."""

    model_config = ConfigDict(from_attributes=True, frozen=True)

    doctor_id: UUID
    granted_at: datetime
    expires_at: datetime
    ended_at: datetime | None
    end_reason: AccessEndReason | None


class PatientSummary(BaseModel):
    """Пациент для врача. `access` — кто спрашивает: ведущий врач или заместитель по сигналу.

    Заместителю отдаются только ФИО, дата рождения, пол, телефон и ведущий врач
    (спека #56, 9.7): почта пустая, клинический профиль не заполнен.
    """

    model_config = ConfigDict(frozen=True)

    id: UUID
    full_name: str
    email: str
    lead_doctor_id: UUID
    created_at: datetime
    phone: str | None
    birth_date: date | None = None
    sex: Sex | None = None
    pregnant: bool | None = None
    breastfeeding: bool | None = None
    egfr: float | None = None
    egfr_measured_on: date | None = None
    child_pugh: ChildPugh | None = None
    # Пациент отозвал согласие (спека #112): канал пациента закрыт, данные у врача остаются.
    consent_revoked_at: datetime | None = None
    access: PatientAccess = "lead"


class PhoneUpdate(BaseModel):
    """Телефон пациента от врача. `None` стирает номер."""

    model_config = ConfigDict(frozen=True)

    phone: str | None

    @field_validator("phone")
    @classmethod
    def _normalize(cls, value: str | None) -> str | None:
        """Убрать пробелы, скобки и дефисы; российский `8XXXXXXXXXX` привести к `+7…`."""
        if value is None:
            return None
        digits = _SEPARATORS.sub("", value)
        if len(digits) == 11 and digits.startswith("8") and digits.isascii() and digits.isdigit():
            digits = "+7" + digits[1:]
        if not _E164.fullmatch(digits):
            raise ValueError("phone must be in E.164 format, e.g. +79000000000")
        return digits


class ProfileUpdate(BaseModel):
    """Клинический профиль от врача. `None` стирает значение, поле не из запроса не меняется."""

    model_config = ConfigDict(frozen=True)

    birth_date: date | None
    sex: Sex | None
    pregnant: bool | None = None
    breastfeeding: bool | None = None
    # СКФ, мл/мин/1,73 м², и дата анализа.
    egfr: float | None = Field(default=None, gt=0, le=200)
    egfr_measured_on: date | None = None
    child_pugh: ChildPugh | None = None

    @field_validator("birth_date", "egfr_measured_on")
    @classmethod
    def _plausible(cls, value: date | None) -> date | None:
        if value is not None and not date(1900, 1, 1) <= value <= date.today():
            raise ValueError("date must be between 1900-01-01 and today")
        return value


class DiagnosisIn(BaseModel):
    """Диагноз от врача: код МКБ-10 (регистр и пробелы по краям не важны) и название."""

    model_config = ConfigDict(frozen=True)

    icd10: Icd10
    title: str = Field(min_length=1, max_length=200)

    @field_validator("icd10", mode="before")
    @classmethod
    def _normalize(cls, value: object) -> object:
        return value.strip().upper() if isinstance(value, str) else value


class DiagnosisOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    icd10: str
    title: str
    set_by: UUID
    set_at: datetime
    resolved_at: datetime | None
    resolved_by: UUID | None


MoodPalette = Literal["warm", "green", "calm"]
StoryMode = Literal["voice", "text"]
TextSize = Literal["normal", "large", "extra_large"]


class PatientPreferencesUpdate(BaseModel):
    """Частичное изменение настроек пациента с проверкой версии."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    expected_revision: int = Field(strict=True, ge=0)
    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    reminder_time: str | None = None
    mood_palette: MoodPalette | None = None
    story_mode: StoryMode | None = None
    text_size: TextSize | None = None

    @field_validator("display_name", mode="before")
    @classmethod
    def _trim_display_name(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("reminder_time")
    @classmethod
    def _validate_reminder_time(cls, value: str | None) -> str | None:
        if value is not None and not _REMINDER_TIME.fullmatch(value):
            raise ValueError("reminder_time must use HH:MM with quarter-hour minutes")
        return value

    @model_validator(mode="after")
    def _reject_null_editable_values(self) -> Self:
        """Разрешить пропуск поля, но запретить null для редактируемых значений."""
        for name in ("display_name", "reminder_time", "mood_palette", "story_mode", "text_size"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        return self


class PatientPreferencesOut(BaseModel):
    """Эффективные настройки пациента и версия для следующего изменения."""

    model_config = ConfigDict(frozen=True)

    display_name: str
    reminder_time: str
    mood_palette: MoodPalette
    story_mode: StoryMode
    text_size: TextSize
    onboarding_completed_at: datetime | None
    revision: int = Field(ge=0)
    updated_at: datetime | None
    timezone: str


class AcceptedConsentOut(BaseModel):
    """Версия согласия, принятая текущим пациентом, и известный текст этой версии."""

    model_config = ConfigDict(frozen=True)

    version: str
    given_at: datetime
    text: str | None
