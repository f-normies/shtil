"""Проверка утверждений ответа вторым проходом LLM (спека #92, 5.3).

Модель получает вопрос, сведения о пациенте и утверждения с цитатами и решает по каждому:
следует ли оно из цитаты и не противоречит ли сведениям о пациенте. Ответ — объект с
обязательным полем на метку (`{"C1": true}`): пропустить утверждение модель не может.
"""

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, create_model
from pydantic_ai import Agent, NativeOutput
from pydantic_ai.settings import ModelSettings

from clin_compass.core.ai.llm import Llm
from clin_compass.modules.knowledge.schemas import Claim

PROMPTS = Path(__file__).parent / "prompts"
TASK = "knowledge.verify"
_SETTINGS = ModelSettings(max_tokens=512, temperature=0)
_INPUT = "{context}Вопрос врача:\n<вопрос>\n{question}\n</вопрос>\n\nУтверждения:\n\n{claims}"
_CONTEXT = "Сведения о пациенте:\n<пациент>\n{context}\n</пациент>\n\n"
_CLAIM = "[{label}] Утверждение: {statement}\nЦитата: {quote}"


def _labels(count: int) -> list[str]:
    return [f"C{n}" for n in range(1, count + 1)]


def _output(count: int) -> type[BaseModel]:
    fields: dict[str, Any] = {label: (bool, ...) for label in _labels(count)}
    return create_model("Verdicts", **fields)


class Verifier:
    """Вердикт по каждому утверждению ответа: показать врачу или нет."""

    def __init__(self, llm: Llm) -> None:
        self._llm = llm
        self.instructions = (PROMPTS / "verify.md").read_text(encoding="utf-8")
        seen = json.dumps([self.instructions, _INPUT, _CONTEXT, _CLAIM], ensure_ascii=False)
        self.prompt_version = f"{TASK}-{hashlib.sha256(seen.encode()).hexdigest()[:8]}"
        self._agents: dict[int, Agent[None, Any]] = {}

    def _agent(self, count: int) -> Agent[None, Any]:
        if count not in self._agents:
            self._agents[count] = Agent(
                output_type=NativeOutput(_output(count)),
                instructions=self.instructions,
                model_settings=_SETTINGS,
            )
        return self._agents[count]

    async def check(
        self, question: str, claims: Sequence[Claim], *, context: str | None = None
    ) -> list[bool]:
        """`True` — утверждение можно показать; порядок — как у `claims`.

        Raises:
            NodesUnavailableError: Ни один узел LLM не принял запрос.
            AiServiceError, AiNoAnswerError: Узел ответил ошибкой или не ответил.
            AiOutputError: Модель ответила не по схеме.
        """
        if not claims:
            return []
        labels = _labels(len(claims))
        body = "\n\n".join(
            _CLAIM.format(label=label, statement=claim.statement, quote=claim.quote)
            for label, claim in zip(labels, claims, strict=True)
        )
        prompt = _INPUT.format(
            context=_CONTEXT.format(context=context) if context else "",
            question=question,
            claims=body,
        )
        result = await self._llm.run(
            self._agent(len(claims)), prompt, task=TASK, prompt_version=self.prompt_version
        )
        verdicts = result.output.model_dump()
        return [bool(verdicts[label]) for label in labels]
