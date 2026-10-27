"""Группа препаратов для взаимодействий: `drug-groups/<id>.yaml` (спека #29, раздел 4).

Состав — МНН в начальной форме. Перечни в инструкциях часто даны примерами, поэтому
состав группы — решение медика с источником.
"""

from pydantic import Field

from clin_compass_knowledge.schemas.common import Id, Model, NonEmpty, SourceRef


class DrugGroup(Model):
    id: Id
    title: NonEmpty
    inn: tuple[NonEmpty, ...] = Field(min_length=1)
    sources: tuple[SourceRef, ...] = Field(min_length=1)
