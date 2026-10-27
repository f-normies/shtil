"""Клиент LLM: узлы vllm по порядку, рассуждения выключены, каждый запуск в ai_runs (спека #18).

LLM не вызывается: узлы — поддельные HTTP-серверы с OpenAI-совместимым ответом.
"""

import asyncio
import json
import traceback
from typing import Any

import httpx2
import pytest
import structlog
from pydantic import BaseModel
from pydantic_ai import Agent, NativeOutput

from clin_compass.core.ai.llm import Llm, build_llm_model
from clin_compass.core.ai.nodes import (
    AiNoAnswerError,
    AiNotConfiguredError,
    AiOutputError,
    AiServiceError,
    NodesUnavailableError,
)
from clin_compass.core.config import Settings
from tests.core.fake_nodes import (
    MemoryRecorder,
    Reply,
    Request,
    assert_no_ambient_openai_values,
    dead_node_url,
    fake_node,
    set_ambient_openai_env,
)

PROMPT = "Принимает Авиандр 50 мг утром."


class Drug(BaseModel):
    name: str
    dose_mg: int


def _agent() -> Agent[None, Drug]:
    return Agent(output_type=NativeOutput(Drug), instructions="Извлеки препарат и дозу.")


def _vllm(request: Request) -> Reply:
    """Ответ vllm на /v1/chat/completions: JSON по схеме и счётчики токенов."""
    return _completion(request, {"name": "Авиандр", "dose_mg": 50})


def _completion(request: Request, answer: dict[str, Any]) -> Reply:
    body = request.json()
    content = json.dumps(answer, ensure_ascii=False)
    return 200, {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 0,
        "model": body["model"],
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 42, "completion_tokens": 9, "total_tokens": 51},
    }


def _settings(*urls: str, **overrides: Any) -> Settings:
    # Значения передаём явно: Makefile экспортирует .env, где может стоять настоящий адрес.
    return Settings(
        database_url="postgresql+asyncpg://unused/none",
        environment="test",
        llm_urls=urls,
        **overrides,
    )


async def test_answer_comes_from_next_node_when_first_is_down() -> None:
    recorder = MemoryRecorder()

    async with fake_node(_vllm) as live:
        llm = Llm(build_llm_model(_settings(dead_node_url(), live.url)), recorder)
        result = await llm.run(_agent(), PROMPT, task="extraction", prompt_version="extraction-v1")

    assert result.output == Drug(name="Авиандр", dose_mg=50)
    [run] = recorder.runs
    assert (run.role, run.task, run.status, run.node) == ("llm", "extraction", "ok", live.url)
    assert (run.model, run.prompt_version) == ("qwen3.6-35b-a3b", "extraction-v1")
    assert (run.input_tokens, run.output_tokens) == (42, 9)


async def test_request_turns_thinking_off_and_asks_for_json_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_ambient_openai_env(monkeypatch)

    async with fake_node(_vllm) as live:
        llm = Llm(build_llm_model(_settings(live.url)), MemoryRecorder())
        await llm.run(_agent(), PROMPT, task="extraction", prompt_version="extraction-v1")

    [request] = live.requests
    body = request.json()
    assert request.path == "/v1/chat/completions"
    assert body["model"] == "qwen3.6-35b-a3b"
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert body["max_completion_tokens"] == 4096
    assert body["response_format"]["type"] == "json_schema"
    assert_no_ambient_openai_values(request)


async def test_model_settings_are_sent_and_recorded_as_params() -> None:
    recorder = MemoryRecorder()

    async with fake_node(_vllm) as live:
        llm = Llm(build_llm_model(_settings(live.url)), recorder)
        await llm.run(
            _agent(),
            PROMPT,
            task="extraction",
            prompt_version="extraction-v1",
            model_settings={"temperature": 0.0, "max_tokens": 512},
        )

    body = live.requests[0].json()
    assert (body["temperature"], body["max_completion_tokens"]) == (0.0, 512)
    assert recorder.runs[0].params == {
        "temperature": 0.0,
        "max_tokens": 512,
        "extra_body": {"chat_template_kwargs": {"enable_thinking": False}},
    }


async def test_server_error_is_not_sent_to_next_node() -> None:
    recorder = MemoryRecorder()

    async with (
        fake_node(lambda request: (500, {"error": {"message": "boom"}})) as broken,
        fake_node(_vllm) as spare,
    ):
        llm = Llm(build_llm_model(_settings(broken.url, spare.url)), recorder)
        with pytest.raises(AiServiceError) as caught:
            await llm.run(_agent(), PROMPT, task="extraction", prompt_version="extraction-v1")

    assert len(broken.requests) == 1
    assert spare.requests == []
    assert (recorder.runs[0].status, recorder.runs[0].error) == ("error", "AiServiceError 500")
    # Тело ответа узла может повторить вход: в ошибку и её цепочку оно не попадает.
    assert caught.value.status == 500
    assert "boom" not in str(caught.value)
    assert caught.value.__cause__ is None


async def test_all_nodes_down_raises_nodes_unavailable_and_is_recorded() -> None:
    recorder = MemoryRecorder()
    llm = Llm(build_llm_model(_settings(dead_node_url(), dead_node_url())), recorder)

    with pytest.raises(NodesUnavailableError):
        await llm.run(_agent(), PROMPT, task="extraction", prompt_version="extraction-v1")

    [run] = recorder.runs
    assert (run.status, run.error, run.node) == ("error", "NodesUnavailableError", None)


async def test_prompt_is_recorded_only_as_hash() -> None:
    recorder = MemoryRecorder()

    async with fake_node(_vllm) as live:
        llm = Llm(build_llm_model(_settings(live.url)), recorder)
        await llm.run(_agent(), PROMPT, task="extraction", prompt_version="extraction-v1")

    [run] = recorder.runs
    assert len(run.input_hash) == 64
    assert "Авиандр" not in repr(run)


def test_build_llm_model_requires_node_urls() -> None:
    with pytest.raises(AiNotConfiguredError, match="COMPASS_LLM_URLS"):
        build_llm_model(_settings())


async def test_task_extra_body_keeps_thinking_off() -> None:
    # top_k и min_p для Qwen уходят в vLLM через extra_body; рассуждения остаются выключены.
    async with fake_node(_vllm) as live:
        llm = Llm(build_llm_model(_settings(live.url)), MemoryRecorder())
        await llm.run(
            _agent(),
            PROMPT,
            task="extraction",
            prompt_version="extraction-v1",
            model_settings={"extra_body": {"top_k": 20, "chat_template_kwargs": {"x": 1}}},
        )

    body = live.requests[0].json()
    assert body["top_k"] == 20
    assert body["chat_template_kwargs"] == {"x": 1, "enable_thinking": False}


async def test_thinking_can_be_turned_on_by_the_task_and_is_recorded() -> None:
    # Вариант ответа с рассуждениями (спека #92, 5.4): сервер отделяет их --reasoning-parser.
    recorder = MemoryRecorder()
    async with fake_node(_vllm) as live:
        llm = Llm(build_llm_model(_settings(live.url)), recorder)
        await llm.run(_agent(), PROMPT, task="knowledge.answer", prompt_version="v1", thinking=True)

    assert live.requests[0].json()["chat_template_kwargs"] == {"enable_thinking": True}
    [run] = recorder.runs
    assert run.params["extra_body"] == {"chat_template_kwargs": {"enable_thinking": True}}


async def test_agent_extra_body_is_sent_and_recorded_as_sent() -> None:
    # PydanticAI сливает настройки поверхностно: extra_body запуска заменил бы extra_body агента.
    recorder = MemoryRecorder()
    agent = Agent(
        output_type=NativeOutput(Drug),
        instructions="Извлеки препарат и дозу.",
        model_settings={"extra_body": {"top_k": 20, "min_p": 0.0}},
    )

    async with fake_node(_vllm) as live:
        llm = Llm(build_llm_model(_settings(live.url)), recorder)
        await llm.run(
            agent,
            PROMPT,
            task="extraction",
            prompt_version="extraction-v1",
            model_settings={"extra_body": {"min_p": 0.05}},
        )

    body = live.requests[0].json()
    assert (body["top_k"], body["min_p"]) == (20, 0.05)
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert recorder.runs[0].params["extra_body"] == {
        "top_k": 20,
        "min_p": 0.05,
        "chat_template_kwargs": {"enable_thinking": False},
    }


async def test_agent_settings_are_recorded_and_unserializable_values_do_not_break_record() -> None:
    recorder = MemoryRecorder()
    agent = Agent(
        output_type=NativeOutput(Drug),
        instructions="Извлеки препарат и дозу.",
        model_settings={"temperature": 0.2, "seed": 7},
    )

    async with fake_node(_vllm) as live:
        llm = Llm(build_llm_model(_settings(live.url)), recorder)
        await llm.run(
            agent,
            PROMPT,
            task="extraction",
            prompt_version="extraction-v1",
            model_settings={"temperature": 0.0, "timeout": httpx2.Timeout(30.0)},
        )

    params = recorder.runs[0].params
    assert (params["temperature"], params["seed"]) == (0.0, 7)
    assert isinstance(params["timeout"], str)
    json.dumps(params)


async def test_answer_off_schema_raises_output_error_without_model_values() -> None:
    # Значение из ответа модели взято из медицинского текста: в ошибку и лог worker'а оно
    # не попадает ни текстом, ни цепочкой причин.
    marker = "МАРКЕР-ИЗ-ОТВЕТА пациент принимает Авиандр"
    recorder = MemoryRecorder()

    def off_schema(request: Request) -> Reply:
        return _completion(request, {"name": marker, "dose_mg": "пятьдесят"})

    async with fake_node(off_schema) as live:
        llm = Llm(build_llm_model(_settings(live.url)), recorder)
        with pytest.raises(AiOutputError) as caught:
            await llm.run(_agent(), PROMPT, task="extraction", prompt_version="extraction-v1")

    error = caught.value
    assert error.role == "llm"
    assert error.__cause__ is None
    assert error.__suppress_context__
    assert marker not in str(error)
    assert marker not in repr(error)
    assert marker not in "".join(traceback.format_exception(error))
    assert recorder.runs[0].error == "AiOutputError"


async def test_response_timeout_is_not_sent_to_next_node() -> None:
    # Запрос дошёл до модели: повтор на другом узле удвоил бы нагрузку. Повторяет очередь.
    recorder = MemoryRecorder()

    async def hang(request: Request) -> Reply:
        await asyncio.sleep(1)
        return _vllm(request)

    async with fake_node(hang) as slow, fake_node(_vllm) as spare:
        llm = Llm(
            build_llm_model(_settings(slow.url, spare.url, llm_timeout_seconds=0.2)), recorder
        )
        with pytest.raises(AiNoAnswerError) as caught:
            await llm.run(_agent(), PROMPT, task="extraction", prompt_version="extraction-v1")

    assert len(slow.requests) == 1
    assert spare.requests == []
    assert caught.value.__cause__ is None
    assert recorder.runs[0].error == "AiNoAnswerError"


async def test_node_switch_is_logged_without_request_content() -> None:
    dead = dead_node_url()

    async with fake_node(_vllm) as live:
        llm = Llm(build_llm_model(_settings(dead, live.url)), MemoryRecorder())
        with structlog.testing.capture_logs() as logs:
            await llm.run(_agent(), PROMPT, task="extraction", prompt_version="extraction-v1")

    assert logs == [
        {
            "event": "ai.node_unavailable",
            "log_level": "warning",
            "role": "llm",
            "node": dead,
            "error": "ModelAPIError",
        }
    ]


async def test_model_name_can_be_chosen_for_candidates() -> None:
    # Подбор моделей (#70) запускает на узле другую LLM под своим именем.
    async with fake_node(_vllm) as live:
        llm = Llm(build_llm_model(_settings(live.url), model="gemma-4-26b-a4b"), MemoryRecorder())
        await llm.run(_agent(), PROMPT, task="extraction", prompt_version="extraction-v1")

    assert live.requests[0].json()["model"] == "gemma-4-26b-a4b"
