"""Синтетическая история болезни: `cases/<id>/` (спека, 4.6)."""

from typing import Annotated, Literal

from pydantic import Field

from clin_compass_knowledge.schemas.common import Id, Model, NonEmpty

Icd10 = Annotated[str, Field(pattern=r"^[A-Z]\d{2}(\.\d{1,2})?$")]


class CaseMeta(Model):
    """`meta.yaml`: признаки шаблона истории."""

    section_order: tuple[NonEmpty, ...] = Field(min_length=1)
    detail: Literal["brief", "standard", "detailed"]
    style: NonEmpty
    diagnosis_format: NonEmpty


class Diagnosis(Model):
    """Диагноз с кодом МКБ-10."""

    icd10: Icd10
    text: NonEmpty


class Medication(Model):
    """Препарат по последнему назначению; None — если в тексте этого нет.

    `name` — русское название, как в ГРЛС, даже если в тексте латиница. `dose` — разовая доза
    числом и единицей по-русски (`0,5 мг`), при одной суточной в тексте — `100 мг/сут`.
    `regimen` — кратность, время приёма, связь с едой, «при …», без длительности курса.
    """

    name: NonEmpty
    dose: NonEmpty | None
    regimen: NonEmpty | None
    status: Literal["current", "past"]  # текущее назначение или прошлый приём


class Fact(Model):
    """Один факт: короткая формулировка без домыслов и дословный фрагмент `history.md`.

    По цитате eval сопоставляет факт с ответом модели.
    """

    fact: NonEmpty
    quote: NonEmpty


class Expected(Model):
    """`expected.json`: эталон извлечения.

    Всё — списки отдельных фактов, чтобы по каждому полю считать точность и полноту.
    """

    diagnoses: tuple[Diagnosis, ...]
    medications: tuple[Medication, ...]
    risk_factors: tuple[Fact, ...]
    anamnesis: tuple[Fact, ...]


class Case(Model):
    """История болезни целиком: текст, признаки шаблона, эталон."""

    id: Id
    history: NonEmpty
    meta: CaseMeta
    expected: Expected
