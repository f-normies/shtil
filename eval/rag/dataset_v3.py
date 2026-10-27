"""Набор вопросов RAG v3 (спека #92, раздел 3): формат, проверки, загрузка.

Вопросы и черновой эталон подготовлены LLM вне репозитория; сюда их переносит
`import_v3.py`. У вопроса есть пункты эталона: пересказ правильного ответа своими словами.
У пункта есть признак «важный» и доказательства — фрагменты индекса, в которых разметчик нашёл
цитату. Самих цитат в наборе нет: условия копирования рекомендаций не указаны.

`kind` нужен метрикам поиска v2 (`dataset.relevant_keys`): `none` — у вопроса нет пунктов,
`multi` — доказательства в двух фрагментах и больше, `single` — в одном.
"""

from collections.abc import Sequence
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from dataset import Kind, Relevant, Split, dump_questions, load_models

QUESTIONS_V3 = Path(__file__).resolve().parent / "questions_v3.yaml"
QuestionType = Literal["reference", "case", "none", "trap"]
Style = Literal["short", "full"]


class Evidence(BaseModel):
    """Фрагмент, где разметчик нашёл цитату пункта: ключ и отпечаток текста."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str = Field(min_length=1)
    fingerprint: str = Field(pattern=r"^[0-9a-f]{12}$")


class Point(BaseModel):
    """Пункт эталона: пересказ, признак «важный» и доказательства."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str = Field(min_length=1)
    critical: bool
    evidence: list[Evidence] = Field(min_length=1)


class QuestionV3(BaseModel):
    """Вопрос набора v3.

    `type` — вид по разметке: справка, случай, нет ответа в базе, ловушка. `style`: `short` —
    коротко, как в мессенджере; `full` — полными фразами. `must_not` — чего не должно быть
    в ответе.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    patient: str | None = None
    type: QuestionType
    asker: str = Field(min_length=1)
    style: Style
    kind: Kind
    split: Split
    points: list[Point]
    must_not: list[str]
    relevant: list[Relevant]
    note: str | None = None

    @model_validator(mode="after")
    def _kind_matches_points(self) -> Self:
        evidence = {e.key for p in self.points for e in p.evidence}
        best = max((r.grade for r in self.relevant), default=0)
        if self.kind == "none":
            if self.points:
                raise ValueError("у вопроса kind: none нет пунктов")
            if best == 2:
                raise ValueError("у вопроса kind: none нет фрагментов с оценкой 2")
            return self
        if not self.points:
            raise ValueError(f"у вопроса kind: {self.kind} нужен хотя бы один пункт")
        if self.kind == "multi" and len(evidence) < 2:
            raise ValueError("у вопроса kind: multi доказательства в двух фрагментах и больше")
        if self.kind == "single" and len(evidence) != 1:
            raise ValueError("у вопроса kind: single доказательства в одном фрагменте")
        graded = {r.key for r in self.relevant if r.grade == 2}
        if not evidence <= graded:
            raise ValueError("фрагменты доказательств должны быть в relevant с оценкой 2")
        return self


def evidence_keys(question: QuestionV3) -> set[str]:
    """Фрагменты-доказательства всех пунктов вопроса."""
    return {e.key for p in question.points for e in p.evidence}


def load_questions_v3(path: Path = QUESTIONS_V3) -> list[QuestionV3]:
    """Набор v3: список YAML; повтор `id` — ошибка."""
    return load_models(path, QuestionV3)


def dump_questions_v3(questions: Sequence[QuestionV3], path: Path, *, header: str) -> None:
    """Пишет набор v3 в YAML: шапка-комментарий, затем список вопросов."""
    dump_questions(questions, path, header=header)
