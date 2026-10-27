"""Правило алерта или маршрутизации: `rules/<id>.yaml` (спека, 4.4)."""

from typing import Literal, Self

from pydantic import Field, model_validator

from clin_compass_knowledge.schemas.common import Id, Model, NonEmpty, SourceRef, Status

Op = Literal[">=", ">", "<=", "<", "=="]

# Ответы и баллы по шкалам: {scale_id: {item_id или score_id: число}}.
Results = dict[Id, dict[Id, float]]


class Condition(Model):
    """Узел условия: `any`/`all` со списком узлов или лист-сравнение.

    Одна модель вместо объединения типов: так путь до ошибки остаётся читаемым
    (`when.any[0].item`), без служебных меток вариантов.
    """

    any: tuple["Condition", ...] | None = None
    all: tuple["Condition", ...] | None = None
    scale: Id | None = None
    item: Id | None = None
    score: Id | None = None
    op: Op | None = None
    value: float | None = None

    @model_validator(mode="after")
    def _check_shape(self) -> Self:
        leaf = (self.scale, self.item, self.score, self.op, self.value)
        is_leaf = any(v is not None for v in leaf)
        kinds = [self.any is not None, self.all is not None, is_leaf]
        if sum(kinds) != 1:
            raise ValueError("узел — это ровно одно из: any, all или сравнение")
        if self.any == () or self.all == ():
            raise ValueError("пустой список условий")
        if is_leaf:
            if self.scale is None or self.op is None or self.value is None:
                raise ValueError("у сравнения нужны scale, op и value")
            if (self.item is None) == (self.score is None):
                raise ValueError("у сравнения нужно ровно одно из: item или score")
        return self

    def leaves(self) -> list["Condition"]:
        """Все листья-сравнения дерева."""
        children = self.any or self.all
        if children is None:
            return [self]
        return [leaf for child in children for leaf in child.leaves()]


class RuleCase(Model):
    """Табличный тест правила."""

    given: Results
    fires: bool
    source: SourceRef


class Rule(Model):
    """Детерминированное правило по данным шкал."""

    id: Id
    version: int = Field(ge=1)
    status: Status
    kind: Literal["alert", "routing"]
    level: Literal["mandatory", "desirable"]
    title: NonEmpty
    when: Condition
    doctor_actions: tuple[NonEmpty, ...] = Field(min_length=1)
    sources: tuple[SourceRef, ...] = Field(min_length=1)
    cases: tuple[RuleCase, ...] = Field(min_length=1)
