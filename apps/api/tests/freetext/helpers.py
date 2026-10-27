"""Модель для тестов freetext: `FunctionModel` отвечает заранее заданным JSON."""

import json
from dataclasses import dataclass
from typing import Any

from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import RequestUsage

from clin_compass.core.ai.llm import MODEL_ALIAS


@dataclass(frozen=True)
class Call:
    """Что ушло в модель: инструкции агента, вход и схема ответа."""

    instructions: str
    prompt: str
    schema: dict[str, Any]


class Script:
    """Отвечает ответами по очереди, последний повторяет. Запоминает каждый вызов.

    Ответ-исключение выбрасывается вместо ответа: так тест изображает сбой узла.
    """

    def __init__(self, *answers: dict[str, Any] | Exception) -> None:
        self._answers = list(answers)
        self.calls: list[Call] = []
        self.model = FunctionModel(self._respond, model_name=MODEL_ALIAS)

    async def _respond(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        request = messages[-1]
        assert isinstance(request, ModelRequest)
        prompt = "".join(
            p.content
            for p in request.parts
            if isinstance(p, UserPromptPart) and isinstance(p.content, str)
        )
        output = info.model_request_parameters.output_object
        self.calls.append(
            Call(info.instructions or "", prompt, dict(output.json_schema) if output else {})
        )
        answer = self._answers.pop(0) if len(self._answers) > 1 else self._answers[0]
        if isinstance(answer, Exception):
            raise answer
        return ModelResponse(
            parts=[TextPart(json.dumps(answer, ensure_ascii=False))],
            usage=RequestUsage(input_tokens=120, output_tokens=30),
        )
