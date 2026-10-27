"""Переписывание вопроса врача в поисковые запросы (спека #92, 5.1).

Режимы: `one` — один запрос, `multi` — до трёх, `patient` — вопрос и сведения о пациенте
склеиваются без LLM, `off` — поиск по вопросу как есть. Запросы ищутся вместе с исходным
вопросом: ошибка переписывания не теряет найденное по нему.
"""

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from string import Template
from typing import Literal

from pydantic import BaseModel, Field, create_model
from pydantic_ai import Agent, NativeOutput
from pydantic_ai.settings import ModelSettings

from clin_compass.core.ai.llm import Llm

PROMPTS = Path(__file__).parent / "prompts"
TASK = "knowledge.rewrite"
Rewrite = Literal["off", "one", "multi", "patient"]
# По какому запросу ранжировщик оценивает кандидатов: по переписанному (#92) или по
# исходному вопросу (#105, 5.2): тогда неверное условие ловушки не попадает в его запрос.
RerankQuery = Literal["rewrite", "question"]
_COUNTS = {"one": 1, "multi": 3}
_SETTINGS = ModelSettings(max_tokens=512, temperature=0)
_INPUT = "{context}Вопрос врача:\n<вопрос>\n{question}\n</вопрос>"
_CONTEXT = "Сведения о пациенте:\n<пациент>\n{context}\n</пациент>\n\n"


def distinct_rewrites(question: str, rewrites: Sequence[str]) -> list[str]:
    """Запросы без тех, что повторяют вопрос: вопрос и так ищется."""
    asked = question.strip().casefold()
    return [q for q in rewrites if q.strip().casefold() != asked]


class Queries(BaseModel):
    """Поисковые запросы; длина списка сужается при сборке агента."""

    queries: list[str]


class Rewriter:
    """Агент переписывания: вопрос и сведения о пациенте → поисковые запросы."""

    def __init__(self, llm: Llm, *, mode: Literal["one", "multi"]) -> None:
        self._llm = llm
        self.count = _COUNTS[mode]
        self.instructions = Template(
            (PROMPTS / "rewrite.md").read_text(encoding="utf-8")
        ).substitute(count=self.count)
        output = create_model(
            "SearchQueries",
            __base__=Queries,
            queries=(list[str], Field(min_length=1, max_length=self.count)),
        )
        seen = json.dumps(
            [self.instructions, _INPUT, _CONTEXT, output.model_json_schema()],
            ensure_ascii=False,
            sort_keys=True,
        )
        self.prompt_version = f"{TASK}-{hashlib.sha256(seen.encode()).hexdigest()[:8]}"
        self._agent: Agent[None, Queries] = Agent(
            output_type=NativeOutput(output),
            instructions=self.instructions,
            model_settings=_SETTINGS,
        )

    async def rewrite(self, question: str, *, context: str | None = None) -> list[str]:
        """Непустые запросы без повторов, не больше `count`.

        Raises:
            NodesUnavailableError: Ни один узел LLM не принял запрос.
            AiServiceError, AiNoAnswerError: Узел ответил ошибкой или не ответил.
            AiOutputError: Модель ответила не по схеме.
        """
        prompt = _INPUT.format(
            context=_CONTEXT.format(context=context) if context else "", question=question
        )
        result = await self._llm.run(
            self._agent, prompt, task=TASK, prompt_version=self.prompt_version
        )
        queries = [q.strip() for q in result.output.queries if q.strip()]
        return list(dict.fromkeys(queries))[: self.count]
