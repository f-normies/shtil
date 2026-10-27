"""`Llm.run` без сети: модель — `FunctionModel` PydanticAI (спека #18, раздел 7).

Образец для тестов задач: агент задачи с `NativeOutput`, ответ модели — функция теста.
"""

import hashlib
import json

import pytest
from pydantic import BaseModel
from pydantic_ai import Agent, NativeOutput
from pydantic_ai.messages import BinaryContent, ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RequestUsage

from clin_compass.core.ai.llm import MODEL_ALIAS, Llm
from tests.core.fake_nodes import MemoryRecorder

PROMPT = "Принимает Авиандр 50 мг утром."
# Синтетические байты вместо снимка: клиенту важны только они сами.
IMAGE = b"\x89PNG\r\n\x1a\n synthetic"


class Drug(BaseModel):
    name: str
    dose_mg: int


def _agent(settings: ModelSettings | None = None) -> Agent[None, Drug]:
    return Agent(
        output_type=NativeOutput(Drug),
        instructions="Извлеки препарат и дозу.",
        model_settings=settings,
    )


class ScriptedModel:
    """`FunctionModel`, который отвечает по схеме и запоминает настройки каждого запроса."""

    def __init__(self) -> None:
        self.settings: list[ModelSettings | None] = []
        self.model = FunctionModel(self._respond, model_name=MODEL_ALIAS)

    async def _respond(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        self.settings.append(info.model_settings)
        answer = json.dumps({"name": "Авиандр", "dose_mg": 50}, ensure_ascii=False)
        return ModelResponse(
            parts=[TextPart(answer)], usage=RequestUsage(input_tokens=42, output_tokens=9)
        )


async def test_run_returns_output_and_records_prompt_version_and_tokens() -> None:
    model, recorder = ScriptedModel(), MemoryRecorder()

    result = await Llm(model.model, recorder).run(
        _agent(), PROMPT, task="extraction", prompt_version="extraction-v1"
    )

    assert result.output == Drug(name="Авиандр", dose_mg=50)
    [run] = recorder.runs
    assert (run.role, run.task, run.status, run.prompt_version) == (
        "llm",
        "extraction",
        "ok",
        "extraction-v1",
    )
    assert (run.model, run.input_tokens, run.output_tokens) == (MODEL_ALIAS, 42, 9)
    assert run.input_hash == hashlib.sha256(PROMPT.encode()).hexdigest()


async def test_prompt_with_file_is_hashed_with_file_bytes() -> None:
    recorder = MemoryRecorder()
    prompt = ["Опиши снимок.", BinaryContent(data=IMAGE, media_type="image/png")]

    await Llm(ScriptedModel().model, recorder).run(
        _agent(), prompt, task="extraction", prompt_version="extraction-v1"
    )

    expected = hashlib.sha256("Опиши снимок.".encode() + b"\0" + IMAGE).hexdigest()
    assert recorder.runs[0].input_hash == expected


async def test_default_max_tokens_is_sent_with_thinking_off_and_recorded() -> None:
    # Без лимита зациклившаяся модель писала бы до конца контекста: минуты работы узла.
    model, recorder = ScriptedModel(), MemoryRecorder()

    await Llm(model.model, recorder).run(
        _agent(), PROMPT, task="extraction", prompt_version="extraction-v1"
    )

    expected = {
        "max_tokens": 4096,
        "extra_body": {"chat_template_kwargs": {"enable_thinking": False}},
    }
    assert model.settings == [expected]
    assert recorder.runs[0].params == expected


@pytest.mark.parametrize(
    ("agent_settings", "run_settings", "sent"),
    [
        ({"max_tokens": 1024}, None, 1024),
        (None, {"max_tokens": 512}, 512),
        ({"max_tokens": 1024}, {"max_tokens": 512}, 512),
    ],
)
async def test_task_overrides_default_max_tokens(
    agent_settings: ModelSettings | None, run_settings: ModelSettings | None, sent: int
) -> None:
    model, recorder = ScriptedModel(), MemoryRecorder()

    await Llm(model.model, recorder).run(
        _agent(agent_settings),
        PROMPT,
        task="extraction",
        prompt_version="extraction-v1",
        model_settings=run_settings,
    )

    [settings] = model.settings
    assert settings is not None
    assert settings.get("max_tokens") == sent
    assert recorder.runs[0].params["max_tokens"] == sent
