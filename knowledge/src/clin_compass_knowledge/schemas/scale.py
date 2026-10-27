"""Шкала оценки: `scales/<id>.yaml` (спека, 4.3)."""

from typing import Literal

from pydantic import Field

from clin_compass_knowledge.schemas.common import Id, Model, NonEmpty, SourceRef, Status


class Option(Model):
    """Вариант ответа; `value` — балл за этот ответ."""

    value: int
    text: NonEmpty


class Item(Model):
    """Вопрос шкалы."""

    id: Id
    text: NonEmpty
    options: tuple[Option, ...] = Field(min_length=2)


class Band(Model):
    """Порог тяжести: балл от `min` до `max` включительно."""

    min: int
    max: int
    code: Id
    label: NonEmpty
    source: SourceRef


class Score(Model):
    """Сумма баллов по набору пунктов и её пороги тяжести."""

    id: Id
    items: tuple[Id, ...] = Field(min_length=1)
    bands: tuple[Band, ...] = Field(min_length=1)


class License(Model):
    """Условия использования шкалы. `allowed: false` — использовать нельзя."""

    terms: NonEmpty
    allowed: bool
    source: SourceRef


class Scale(Model):
    """Определение шкалы: вопросы, ответы, подсчёт, пороги."""

    id: Id
    version: int = Field(ge=1)
    title: NonEmpty
    purpose: Literal["screening", "monitoring"]
    respondent: Literal["patient", "clinician"]
    status: Status
    license: License
    validation: tuple[SourceRef, ...] = Field(min_length=1)
    instruction: NonEmpty
    items: tuple[Item, ...] = Field(min_length=1)
    scores: tuple[Score, ...] = Field(min_length=1)

    def item(self, item_id: str) -> Item | None:
        """Пункт по id или None."""
        return next((i for i in self.items if i.id == item_id), None)

    def score_def(self, score_id: str) -> Score | None:
        """Сумма по id или None."""
        return next((s for s in self.scores if s.id == score_id), None)
