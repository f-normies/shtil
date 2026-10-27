"""LLM-судьи подбора RAG (спека #70, 3.2 и 6.4).

`RelevanceJudge` оценивает фрагменты выдачи для вопроса: 2 — отвечает, 1 — частично, 0 — нет.
`ClaimJudge` проверяет утверждения ответа: следует ли утверждение из своей цитаты.
`PointJudge` и `MustNotJudge` (спека #92, 6.1–6.2) сверяют ответ с эталоном набора v3: есть ли
в ответе пункт эталона и есть ли в нём то, чего быть не должно.
Ответ — объект с обязательным полем на каждую метку: `{"F1": 2, "F2": 0}`. Так ответ короткий,
и пропустить метку модель не может. При ответе не по схеме или без ответа вызов повторяется,
после трёх попыток — ошибка.
"""

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from clin_compass.core.ai.llm import Llm
from clin_compass.core.ai.nodes import AiNoAnswerError, AiOutputError
from pydantic import BaseModel, create_model
from pydantic_ai import Agent, NativeOutput
from pydantic_ai.settings import ModelSettings

PROMPTS = Path(__file__).resolve().parent / "prompts"
RELEVANCE_TASK = "eval.rag.judge_relevance"
CLAIM_TASK = "eval.rag.judge_claim"
POINT_TASK = "eval.rag.judge_point"
MUST_NOT_TASK = "eval.rag.judge_must_not"
ATTEMPTS = 3
_SETTINGS = ModelSettings(max_tokens=2048, temperature=0)
_QUESTION = "Вопрос врача:\n<вопрос>\n{question}\n</вопрос>\n\n"
_PASSAGE = "[{label}] {title}. Раздел: {heading}\n{text}"
_CLAIM = "[{label}] Утверждение: {statement}\nЦитата: {quote}"
_PATIENT = "Сведения о пациенте:\n<пациент>\n{patient}\n</пациент>\n\n"
_ANSWER = "[{number}] Утверждение: {statement}\nЦитата: {quote}"
_ITEM = "[{label}] {text}"
Verdict = Literal["supported", "partial", "unsupported"]


@dataclass(frozen=True)
class Passage:
    """Фрагмент, который видит судья релевантности."""

    key: str
    title: str
    heading_path: str
    text: str


def batches(passages: Sequence[Passage], *, max_count: int, max_chars: int) -> list[list[Passage]]:
    """Порции для судьи: не больше `max_count` фрагментов и `max_chars` знаков текста.

    Фрагмент длиннее `max_chars` идёт отдельной порцией.
    """
    result: list[list[Passage]] = []
    current: list[Passage] = []
    size = 0
    for passage in passages:
        length = len(passage.text)
        if current and (len(current) == max_count or size + length > max_chars):
            result.append(current)
            current, size = [], 0
        current.append(passage)
        size += length
    if current:
        result.append(current)
    return result


def _labels(prefix: str, count: int) -> list[str]:
    return [f"{prefix}{n}" for n in range(1, count + 1)]


def _output(name: str, labels: list[str], value: Any) -> type[BaseModel]:
    """Объект с обязательным полем на каждую метку: короткий ответ, пропуск метки невозможен."""
    return create_model(name, **{label: (value, ...) for label in labels})  # type: ignore[call-overload]


def _version(task: str, instructions: str, template: str) -> str:
    seen = json.dumps([instructions, template], ensure_ascii=False)
    return f"{task}-{hashlib.sha256(seen.encode()).hexdigest()[:8]}"


class _Judge:
    """Общее: агенты по числу меток и повтор вызова при сбое модели."""

    def __init__(
        self, llm: Llm, *, prompt: str, task: str, template: str, name: str, value: Any
    ) -> None:
        self._llm = llm
        self._task = task
        self._instructions = (PROMPTS / prompt).read_text(encoding="utf-8")
        self._name = name
        self._value = value
        self._agents: dict[int, Agent[None, Any]] = {}
        self.prompt_version = _version(task, self._instructions, template)

    def _agent(self, labels: list[str]) -> Agent[None, Any]:
        if len(labels) not in self._agents:
            self._agents[len(labels)] = Agent(
                output_type=NativeOutput(_output(self._name, labels, self._value)),
                instructions=self._instructions,
                model_settings=_SETTINGS,
            )
        return self._agents[len(labels)]

    async def _ask(self, prompt: str, labels: list[str]) -> dict[str, Any]:
        for attempt in range(1, ATTEMPTS + 1):
            try:
                result = await self._llm.run(
                    self._agent(labels), prompt, task=self._task, prompt_version=self.prompt_version
                )
            except (AiOutputError, AiNoAnswerError):
                if attempt == ATTEMPTS:
                    raise
                continue
            return dict(result.output.model_dump())
        raise AssertionError("недостижимо")


class RelevanceJudge(_Judge):
    """Оценки фрагментов для вопроса: 2 — отвечает, 1 — частично, 0 — нет."""

    def __init__(self, llm: Llm) -> None:
        super().__init__(
            llm,
            prompt="judge_relevance.md",
            task=RELEVANCE_TASK,
            template=_QUESTION + _PASSAGE,
            name="Grades",
            value=Literal[0, 1, 2],
        )

    async def grade(self, question: str, passages: Sequence[Passage]) -> dict[str, int]:
        """Ключ фрагмента → оценка для одной порции фрагментов."""
        labels = _labels("F", len(passages))
        body = "\n\n".join(
            _PASSAGE.format(label=lab, title=p.title, heading=p.heading_path, text=p.text)
            for lab, p in zip(labels, passages, strict=True)
        )
        found = await self._ask(
            _QUESTION.format(question=question) + "Фрагменты:\n\n" + body, labels
        )
        return {p.key: int(found[lab]) for lab, p in zip(labels, passages, strict=True)}


class ClaimJudge(_Judge):
    """Вердикты по утверждениям ответа: следует ли утверждение из цитаты."""

    def __init__(self, llm: Llm) -> None:
        super().__init__(
            llm,
            prompt="judge_claim.md",
            task=CLAIM_TASK,
            template=_QUESTION + _CLAIM,
            name="Verdicts",
            value=Verdict,
        )

    async def verdicts(self, question: str, claims: Sequence[tuple[str, str]]) -> list[str]:
        """Вердикт по каждому утверждению `(утверждение, цитата)` в том же порядке."""
        if not claims:
            return []
        labels = _labels("C", len(claims))
        body = "\n\n".join(
            _CLAIM.format(label=lab, statement=statement, quote=quote)
            for lab, (statement, quote) in zip(labels, claims, strict=True)
        )
        found = await self._ask(
            _QUESTION.format(question=question) + "Утверждения:\n\n" + body, labels
        )
        return [str(found[lab]) for lab in labels]


class _AnswerCheck(_Judge):
    """Общее судей ответа по эталону: метка на каждый пункт, ответ `true` или `false`."""

    def __init__(self, llm: Llm, *, prompt: str, task: str, prefix: str, heading: str) -> None:
        super().__init__(
            llm,
            prompt=prompt,
            task=task,
            template=_QUESTION + _PATIENT + _ANSWER + _ITEM + heading,
            name="Checks",
            value=bool,
        )
        self._prefix = prefix
        self._heading = heading

    async def _check(
        self,
        question: str,
        patient: str | None,
        claims: Sequence[tuple[str, str]],
        items: Sequence[str],
    ) -> list[bool]:
        if not items:
            return []
        if not claims:
            return [False] * len(items)
        labels = _labels(self._prefix, len(items))
        answer = "\n\n".join(
            _ANSWER.format(number=n, statement=statement, quote=quote)
            for n, (statement, quote) in enumerate(claims, start=1)
        )
        listed = "\n".join(
            _ITEM.format(label=lab, text=text) for lab, text in zip(labels, items, strict=True)
        )
        prompt = (
            _QUESTION.format(question=question)
            + (_PATIENT.format(patient=patient) if patient else "")
            + f"Ответ ассистента:\n\n{answer}\n\n{self._heading}:\n\n{listed}"
        )
        found = await self._ask(prompt, labels)
        return [bool(found[lab]) for lab in labels]


class PointJudge(_AnswerCheck):
    """Есть ли в ответе каждый пункт эталона (спека #92, 6.1)."""

    def __init__(self, llm: Llm) -> None:
        super().__init__(
            llm, prompt="judge_point.md", task=POINT_TASK, prefix="P", heading="Пункты эталона"
        )

    async def covered(
        self,
        question: str,
        patient: str | None,
        claims: Sequence[tuple[str, str]],
        points: Sequence[str],
    ) -> list[bool]:
        """Вердикт по каждому пункту в том же порядке; пустой ответ — все `False` без вызова."""
        return await self._check(question, patient, claims, points)


class MustNotJudge(_AnswerCheck):
    """Есть ли в ответе то, чего в нём быть не должно (спека #92, 6.2)."""

    def __init__(self, llm: Llm) -> None:
        super().__init__(
            llm,
            prompt="judge_must_not.md",
            task=MUST_NOT_TASK,
            prefix="M",
            heading="Чего не должно быть в ответе",
        )

    async def present(
        self,
        question: str,
        patient: str | None,
        claims: Sequence[tuple[str, str]],
        items: Sequence[str],
    ) -> list[bool]:
        """Вердикт по каждому пункту `must_not`; пустой ответ — все `False` без вызова."""
        return await self._check(question, patient, claims, items)
