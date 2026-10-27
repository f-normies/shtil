"""Публичные схемы модуля ingestion (спека #30, раздел 4)."""

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

DocumentStatus = Literal["parsing", "extracting", "done", "failed"]
ErrorCode = Literal[
    "page_unreadable", "empty_text", "text_too_long", "parse_failed", "extract_failed"
]
# Откуда текст страницы: сведённый со слоем вывод модели, слой PDF без разметки, распознавание.
PageSource = Literal["layer_markup", "layer", "ocr"]
MedicationStatus = Literal["current", "past"]
# Дата отмены написана в цитате (`text`) или вычислена от даты документа (`computed`).
StopDateSource = Literal["text", "computed"]
FactField = Literal["anamnesis", "risk_factor", "past_medication"]
# Виды предложений: диагноз, препарат, факт анамнеза или фактор риска.
DIAGNOSIS_KIND = "ingestion.diagnosis"
MEDICATION_KIND = "ingestion.medication"
FACT_KIND = "ingestion.fact"


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class PageSourceOut(_Frozen):
    page: int
    source: PageSource


class DrugMatchOut(_Frozen):
    trade_name: str | None
    inn: str
    reg_number: str | None


class DrugCheckOut(_Frozen):
    """Сверка названия со снимком ГРЛС: `registered`, `similar`, `not_found` и др."""

    name: str
    check: str
    matches: list[DrugMatchOut]


class _Payload(_Frozen):
    """Общее у всех предложений ingestion: документ, цитата, страница с ней, распознан ли текст."""

    document_id: UUID
    quote: str
    # Первая страница, где есть цитата; None — цитата переходит со страницы на страницу.
    page: int | None
    ocr: bool


class DiagnosisPayload(_Payload):
    """`payload` предложения `ingestion.diagnosis`. `code_in_text` — нет: код предложен моделью."""

    icd10: str
    text: str
    code_in_text: bool


class MedicationPayload(_Payload):
    """`payload` предложения `ingestion.medication`.

    `renamed` — модель изменила название из текста (`name_in_text`) или его нет в цитате.
    `document_date` — на какую дату верен статус `current`. `stopped_on_source` — откуда дата
    отмены: `computed` — вычислена от даты документа, врач видит «дата вычислена от даты
    документа».
    """

    name: str
    name_in_text: str
    renamed: bool
    dose: str | None
    regimen: str | None
    status: MedicationStatus
    stopped_on: date | None
    stopped_on_source: StopDateSource | None
    document_date: date | None
    drug: DrugCheckOut


class FactPayload(_Payload):
    """`payload` предложения `ingestion.fact`: анамнез или фактор риска."""

    field: Literal["anamnesis", "risk_factor"]
    fact: str


class KnownFact(_Frozen):
    """Факт документа, который уже был в карточке: предложения по нему нет."""

    kind: Literal["ingestion.diagnosis", "ingestion.medication"]
    payload: DiagnosisPayload | MedicationPayload


class DocumentOut(_Frozen):
    """Документ без текста и ключа S3. `error_*` — у `failed`: код из раздела 3 спеки."""

    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    patient_id: UUID
    uploaded_by: UUID
    filename: str
    content_type: str
    size: int
    sha256: str
    pages: int
    status: DocumentStatus
    error_code: ErrorCode | None
    error_page: int | None
    page_sources: list[PageSourceOut]
    # None — текст ещё не распознан.
    ocr: bool | None
    created_at: datetime
    parsed_at: datetime | None
    extracted_at: datetime | None
    extract_run_id: UUID | None
    dropped: int | None
    document_date: date | None
    known: list[KnownFact]
    parse_failures: int
    extract_failures: int


class FactOut(_Frozen):
    """Факт из документа, подтверждённый врачом: анамнез, фактор риска, прошлый препарат."""

    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    proposal_id: UUID
    document_id: UUID
    patient_id: UUID
    field: FactField
    # `payload` предложения.
    data: dict[str, Any]
    confirmed_at: datetime
    confirmed_by: UUID
