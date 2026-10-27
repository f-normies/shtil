"""Источник базы знаний: запись `sources/manifest.yaml` (спека, 4.2)."""

import datetime as dt
from typing import Literal, Self

from pydantic import HttpUrl, model_validator

from clin_compass_knowledge.schemas.common import Id, Model, NonEmpty
from clin_compass_knowledge.schemas.drug import Icd10Pattern

# drug_label — инструкция или ОХЛП, источник для врача и правил препарата.
# patient_leaflet — листок-вкладыш для пациента: инструкцией препарата он не считается.
# drug_registry — снимок ГРЛС, который собирает `grls-update` (спека #32, 5.2).
SourceKind = Literal[
    "clinical_guideline",
    "drug_label",
    "patient_leaflet",
    "scale_validation",
    "drug_registry",
    "other",
]


class Source(Model):
    """Документ, на который ссылаются шкалы, правила и сценарии."""

    id: Id
    title: NonEmpty
    kind: SourceKind
    publisher: NonEmpty
    url: HttpUrl
    version: NonEmpty
    date: dt.date
    license: NonEmpty
    file: str | None  # путь относительно sources/; None — хранить копию нельзя
    supplementary: bool  # международный источник, только как дополнение
    # Коды, рубрики или диапазоны МКБ-10, которые покрывает клиническая рекомендация.
    # По ним «Терапия» выбирает КР к диагнозам пациента (спека #107). Сопоставление — медик.
    icd10: tuple[Icd10Pattern, ...] = ()

    @model_validator(mode="after")
    def _codes_only_for_guidelines(self) -> Self:
        if self.kind == "clinical_guideline" and not self.icd10:
            raise ValueError("у клинической рекомендации нужен icd10: коды, которые она покрывает")
        if self.kind != "clinical_guideline" and self.icd10:
            raise ValueError("icd10 задаётся только у клинической рекомендации")
        return self


class Manifest(Model):
    """Файл `sources/manifest.yaml`."""

    sources: tuple[Source, ...]
