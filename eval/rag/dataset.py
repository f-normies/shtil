"""Набор вопросов RAG v2 (спека #70, раздел 3): формат, загрузка, деление и правила отбора.

У вопроса есть фрагменты, по которым его писал генератор (`origin`), и оценки судьи по
объединённой выдаче поиска (`relevant`): 2 — фрагмент отвечает, 1 — отвечает частично.
Оценка 0 не хранится. Текста фрагментов в наборе нет: условия копирования рекомендаций
не указаны. Ключ фрагмента зависит от нарезки, поэтому у каждого есть отпечаток текста,
и скрипты сверяют его с индексом до прогона.
"""

import hashlib
import random
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Literal, Protocol, Self

import yaml
from clin_compass_knowledge.loader import load_yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

QUESTIONS = Path(__file__).resolve().parent / "questions.yaml"
Kind = Literal["single", "multi", "none"]
Split = Literal["dev", "test"]
_WORD = re.compile(r"\w+")


def fingerprint(text: str) -> str:
    """Отпечаток текста фрагмента: первые 12 знаков SHA-256."""
    return hashlib.sha256(text.encode()).hexdigest()[:12]


class Relevant(BaseModel):
    """Фрагмент с оценкой судьи: ключ, отпечаток текста на момент разметки, оценка."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str = Field(min_length=1)
    fingerprint: str = Field(pattern=r"^[0-9a-f]{12}$")
    grade: Literal[1, 2]


class Question(BaseModel):
    """Вопрос врача из набора v2.

    `kind` — замысел генератора: `single` — ответ в одном фрагменте, `multi` — нужны
    несколько, `none` — ответа в базе нет. `split`: `dev` — подбор, `test` — контроль.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    kind: Kind
    split: Split
    origin: list[str]
    relevant: list[Relevant]
    note: str | None = None

    @model_validator(mode="after")
    def _relevant_matches_kind(self) -> Self:
        best = max((r.grade for r in self.relevant), default=0)
        if self.kind == "none":
            if self.origin:
                raise ValueError("у вопроса kind: none список origin пуст")
            if best == 2:
                raise ValueError("у вопроса kind: none нет фрагментов с оценкой 2")
            return self
        if not self.origin:
            raise ValueError(f"у вопроса kind: {self.kind} нужен непустой origin")
        if self.kind == "single" and best < 2:
            raise ValueError("у вопроса kind: single нужен фрагмент с оценкой 2")
        if self.kind == "multi" and not self.relevant:
            raise ValueError("у вопроса kind: multi нужны фрагменты с оценкой 1 или 2")
        return self


class Labeled(Protocol):
    """Вопрос с видом и оценками фрагментов: `Question` v2 и `QuestionV3`."""

    @property
    def kind(self) -> Kind: ...

    @property
    def relevant(self) -> Sequence[Relevant]: ...


def relevant_keys(question: Labeled) -> set[str]:
    """Фрагменты, которые считаются ответом в recall и MRR.

    У `multi` ответ собирается из частей, поэтому релевантна и оценка 1. У остальных — только 2.
    """
    floor = 1 if question.kind == "multi" else 2
    return {r.key for r in question.relevant if r.grade >= floor}


def grades(question: Labeled) -> dict[str, int]:
    """Ключ фрагмента → оценка судьи; для nDCG."""
    return {r.key: r.grade for r in question.relevant}


def load_questions(path: Path = QUESTIONS) -> list[Question]:
    """Набор вопросов: список YAML. Пустой файл или `[]` — пустой набор; повтор `id` — ошибка."""
    return load_models(path, Question)


def load_models[M: BaseModel](path: Path, model: type[M]) -> list[M]:
    """Список YAML в модели `model`; у каждой есть поле `id`, повтор `id` — ошибка."""
    data = load_yaml(path.read_text(encoding="utf-8")) or []
    if not isinstance(data, list):
        raise ValueError(f"{path.name}: нужен список вопросов")
    items = [model.model_validate(raw) for raw in data]
    ids = [str(item.model_dump(include={"id"})["id"]) for item in items]
    repeated = sorted({i for i in ids if ids.count(i) > 1})
    if repeated:
        raise ValueError(f"повторяются id: {repeated}")
    return items


def dump_questions(questions: Sequence[BaseModel], path: Path, *, header: str) -> None:
    """Пишет набор в YAML: шапка-комментарий, затем список вопросов."""
    data = [q.model_dump(exclude_none=True) for q in questions]
    body = yaml.safe_dump(
        data, allow_unicode=True, sort_keys=False, default_flow_style=None, width=1000
    )
    path.write_text(header + body, encoding="utf-8")


def check_index(questions: Sequence[Question], index: Mapping[str, str]) -> list[str]:
    """Расхождения набора с действующим индексом: нет ключа или текст фрагмента изменился.

    Args:
        questions: Набор вопросов.
        index: Ключ фрагмента действующего документа → отпечаток его текста.

    Returns:
        Строка на расхождение; пусто — набор совпадает с индексом.
    """
    problems = []
    for question in questions:
        for relevant in question.relevant:
            current = index.get(relevant.key)
            if current is None:
                problems.append(f"{question.id}: {relevant.key} нет в действующем индексе")
            elif current != relevant.fingerprint:
                problems.append(
                    f"{question.id}: {relevant.key} изменился: в наборе {relevant.fingerprint}, "
                    f"в индексе {current}"
                )
    return problems


def split_ids(
    items: Sequence[tuple[str, str]], *, test_share: float, seed: int
) -> dict[str, Split]:
    """Случайное деление на подбор и контроль внутри каждой группы.

    Args:
        items: Пары «id вопроса, группа»; группа — источник и вид вопроса.
        test_share: Доля контроля в каждой группе, округляется до целого.
        seed: Зерно. Порядок `items` на результат не влияет.

    Returns:
        id вопроса → `dev` или `test`.
    """
    groups: dict[str, list[str]] = {}
    for qid, group in items:
        groups.setdefault(group, []).append(qid)
    result: dict[str, Split] = {}
    for group, ids in sorted(groups.items()):
        ordered = sorted(ids)
        random.Random(f"{seed}/{group}").shuffle(ordered)
        test_count = round(len(ordered) * test_share)
        for position, qid in enumerate(ordered):
            result[qid] = "test" if position < test_count else "dev"
    return result


def _words(text: str) -> list[str]:
    return _WORD.findall(text.lower().replace("ё", "е"))


def shares_ngram(question: str, text: str, *, n: int) -> bool:
    """Есть ли у вопроса с текстом общая цепочка из `n` слов подряд (регистр и «ё» не важны)."""
    words = _words(text)
    grams = {tuple(words[i : i + n]) for i in range(len(words) - n + 1)}
    asked = _words(question)
    return any(tuple(asked[i : i + n]) in grams for i in range(len(asked) - n + 1))


def keep_after_labeling(kind: str, origin: Sequence[str], grades: Mapping[str, int]) -> bool:
    """Остаётся ли вопрос после разметки судьёй (спека #70, 3.2).

    `single` — исходный фрагмент получил 2; `multi` — каждый исходный фрагмент получил хотя бы 1;
    `none` — ни один фрагмент выдачи не получил 2.
    """
    if kind == "none":
        return all(grade < 2 for grade in grades.values())
    floor = 2 if kind == "single" else 1
    return all(grades.get(key, 0) >= floor for key in origin)
