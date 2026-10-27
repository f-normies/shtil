"""Экран помощи пациенту: `patient-help.yaml` (спека #57, раздел 4)."""

import datetime as dt
from typing import Annotated

from pydantic import Field

from clin_compass_knowledge.schemas.common import Id, Model, NonEmpty, SourceRef, Status

# Из номера строится ссылка `tel:`: только цифры, без пробелов и плюса.
PhoneNumber = Annotated[str, Field(pattern=r"^[0-9]{2,15}$")]


class HelpContact(Model):
    """Номер на экране помощи; `primary` — главная кнопка звонка."""

    id: Id
    number: PhoneNumber
    label: NonEmpty
    primary: bool
    sources: tuple[SourceRef, ...] = Field(min_length=1)


class PatientHelp(Model):
    """Файл `patient-help.yaml`: текст экрана, номера и дата их проверки."""

    version: int = Field(ge=1)
    status: Status
    checked_on: dt.date
    title: NonEmpty
    text: NonEmpty
    sources: tuple[SourceRef, ...] = Field(min_length=1)  # источник текста экрана
    contacts: tuple[HelpContact, ...] = Field(min_length=1)
