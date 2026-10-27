"""Публичные схемы модуля knowledge: фрагменты, результаты поиска, ответ с цитатами (спека #27),
источник для инспектора (спека #106)."""

import datetime as dt
from typing import Annotated, Literal
from uuid import UUID

from clin_compass_knowledge.grls import RegistryStatus
from clin_compass_knowledge.schemas.source import SourceKind
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from clin_compass.modules.knowledge.fragments import FragmentKind

DocumentKind = Literal["clinical_guideline", "drug_label"]
# `superseded` — прежняя версия документа: на её фрагменты ссылаются подтверждённые рекомендации.
DocumentStatus = Literal["active", "superseded"]
# Длина вопроса врача (спека #27, раздел 8).
QUESTION_MAX_LENGTH = 1000


class DocumentRef(BaseModel):
    """Документ фрагмента: источник манифеста, название, версия и адрес."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    source_id: str
    title: str
    version: str
    url: str
    kind: DocumentKind
    status: DocumentStatus


class IndexedDocument(BaseModel):
    """Действующая версия документа в индексе: её фиксирует оценка (`eval/rag`)."""

    model_config = ConfigDict(frozen=True)

    source_id: str
    version: str
    title: str
    kind: DocumentKind
    # SHA-256 исходного JSON или PDF.
    content_hash: str


class IndexSummary(BaseModel):
    """Действующие документы индекса: версия и счётчики для health и вывода выгрузки (#109)."""

    model_config = ConfigDict(frozen=True)

    # Первые 12 знаков SHA-256 от строк `source_id:content_hash`; None — индекс пуст.
    version: str | None
    documents: int
    fragments: int


class Fragment(BaseModel):
    """Фрагмент документа с текстом, по которому сверяется цитата."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    # `{source_id}/{раздел}/{номер в разделе}`.
    key: str
    document: DocumentRef
    # `doc_3` у клинической рекомендации или `4.3` у ОХЛП и подпись раздела.
    section: str
    section_title: str
    heading_path: str
    kind: FragmentKind
    text: str
    # Уровень убедительности рекомендаций (A, B, C) и достоверности доказательств (1–5):
    # только у пункта рекомендации.
    uur: str | None
    udd: int | None


class Hit(BaseModel):
    """Фрагмент из поиска: балл RRF и места в векторном и полнотекстовом поиске (с 1)."""

    model_config = ConfigDict(frozen=True)

    fragment: Fragment
    score: float
    # None — фрагмента нет среди лучших в этом поиске.
    vector_rank: int | None
    text_rank: int | None
    # Балл ранжировщика; None — ранжировщика не было или фрагмент дальше его глубины.
    rerank_score: float | None = None


class FragmentRef(BaseModel):
    """Ссылка утверждения на фрагмент: по `id` интерфейс открывает цитату."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    key: str
    source_id: str
    title: str
    version: str
    section: str
    section_title: str
    uur: str | None
    udd: int | None
    # `superseded` — цитата из прежней версии документа.
    status: DocumentStatus


class Claim(BaseModel):
    """Утверждение ответа с дословной цитатой, найденной в тексте фрагмента."""

    model_config = ConfigDict(frozen=True)

    statement: str
    quote: str
    fragment: FragmentRef


class Answer(BaseModel):
    """Ответ по базе знаний: только утверждения с проверенной цитатой.

    `dropped` — сколько утверждений модели отброшено: их цитаты нет во фрагменте.
    `run_id` — строка ai_runs вызова LLM; None, если поиск ничего не нашёл и модель
    не вызывалась.
    """

    model_config = ConfigDict(frozen=True)

    claims: list[Claim]
    dropped: int
    run_id: UUID | None


class AnswerIn(BaseModel):
    """Вопрос врача. `source_ids` сужает поиск до этих источников манифеста."""

    model_config = ConfigDict(extra="forbid")

    # Пробелы по краям обрезаются до проверки длины: вопрос из одних пробелов — 422.
    question: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=QUESTION_MAX_LENGTH),
    ]
    source_ids: list[str] | None = Field(default=None, min_length=1)


class SourceInfo(BaseModel):
    """Документ манифеста knowledge/: название, издатель, версия, дата и адрес (спека #106, 3.4).

    `supplementary` — международный источник, только как дополнение.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    title: str
    kind: SourceKind
    publisher: str
    version: str
    date: dt.date
    url: str
    supplementary: bool


class RegistryInfo(BaseModel):
    """Регистрация препарата инструкции: номер РУ из проверенной карточки и статус в снимке ГРЛС
    на дату снимка (спека #106, 3.5)."""

    model_config = ConfigDict(frozen=True)

    reg_number: str
    status: RegistryStatus
    snapshot_date: dt.date


class SourceSection(BaseModel):
    """Раздел документа с фрагментами в порядке документа.

    `number` — номер раздела (`4.3`, `3.1.3`); None — у фрагмента нет нумерованного заголовка.
    `title` — заголовок раздела без номера; пустой, если в документе нет заголовка с этим номером.
    """

    model_config = ConfigDict(frozen=True)

    number: str | None
    title: str
    fragments: list[Fragment]


class SourceView(BaseModel):
    """Источник для инспектора: документ из манифеста и текст раздела (спека #106, раздел 4).

    `registry` — только у инструкции препарата с проверенной карточкой. `locator` — ссылка
    из данных; None, если запрошен фрагмент по id. `found` — нашёлся ли текст в индексе;
    без него `sections` пуст, а сведения о документе есть.
    """

    model_config = ConfigDict(frozen=True)

    source: SourceInfo
    registry: RegistryInfo | None
    locator: str | None
    found: bool
    sections: list[SourceSection]


__all__ = [
    "QUESTION_MAX_LENGTH",
    "Answer",
    "AnswerIn",
    "Claim",
    "DocumentKind",
    "DocumentRef",
    "DocumentStatus",
    "Fragment",
    "FragmentRef",
    "Hit",
    "IndexSummary",
    "IndexedDocument",
    "RegistryInfo",
    "SourceInfo",
    "SourceSection",
    "SourceView",
]
