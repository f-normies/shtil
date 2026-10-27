"""Общие типы схем: базовая модель, идентификатор, ссылка на источник."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

# Лишнее поле — почти всегда опечатка в YAML, поэтому запрещено.
_CONFIG = ConfigDict(extra="forbid", frozen=True)

Id = Annotated[str, Field(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$")]
NonEmpty = Annotated[str, Field(min_length=1)]
Status = Literal["draft", "reviewed"]


class Model(BaseModel):
    """База всех схем: неизменяемая, без лишних полей."""

    model_config = _CONFIG


class SourceRef(Model):
    """Ссылка на запись манифеста и место в документе."""

    source: Id
    locator: NonEmpty
