"""Вопросы к фрагментам при индексации (спека #105, 5.1).

LLM пишет к фрагменту несколько вопросов языком врача: коротко, как в мессенджере, полными
фразами и о конкретном пациенте. Поиск сравнивает вопрос врача и с текстом фрагмента, и с этими
вопросами: так находятся фрагменты, написанные языком КР, по вопросу со сленгом.
"""

import hashlib
import json
from pathlib import Path
from string import Template

from pydantic import BaseModel, Field, create_model
from pydantic_ai import Agent, NativeOutput
from pydantic_ai.settings import ModelSettings

from clin_compass.core.ai.llm import Llm

PROMPTS = Path(__file__).parent / "prompts"
TASK = "knowledge.fragment_questions"
COUNT = 4
_SETTINGS = ModelSettings(max_tokens=1024, temperature=0)
_INPUT = "Документ: {title}\nРаздел: {heading}\n\nФрагмент:\n<фрагмент>\n{text}\n</фрагмент>"


class FragmentQuestions(BaseModel):
    """Вопросы к фрагменту; их число задаётся при сборке агента."""

    questions: list[str]


class Questioner:
    """Агент вопросов к фрагменту."""

    def __init__(self, llm: Llm) -> None:
        self._llm = llm
        self.instructions = Template(
            (PROMPTS / "fragment_questions.md").read_text(encoding="utf-8")
        ).substitute(count=COUNT)
        output = create_model(
            "FragmentQuestionList",
            __base__=FragmentQuestions,
            questions=(list[str], Field(min_length=COUNT, max_length=COUNT)),
        )
        seen = json.dumps(
            [self.instructions, _INPUT, output.model_json_schema()],
            ensure_ascii=False,
            sort_keys=True,
        )
        self.prompt_version = f"{TASK}-{hashlib.sha256(seen.encode()).hexdigest()[:8]}"
        self._agent: Agent[None, FragmentQuestions] = Agent(
            output_type=NativeOutput(output),
            instructions=self.instructions,
            model_settings=_SETTINGS,
        )

    async def questions(self, title: str, heading: str, text: str) -> list[str]:
        """Непустые вопросы к фрагменту без повторов.

        Raises:
            NodesUnavailableError: Ни один узел LLM не принял запрос.
            AiServiceError, AiNoAnswerError: Узел ответил ошибкой или не ответил.
            AiOutputError: Модель ответила не по схеме.
        """
        result = await self._llm.run(
            self._agent,
            _INPUT.format(title=title, heading=heading, text=text),
            task=TASK,
            prompt_version=self.prompt_version,
        )
        cleaned = [q.strip() for q in result.output.questions if q.strip()]
        return list(dict.fromkeys(cleaned))
