"""Клиент LLM: Qwen3.6-35B-A3B на узлах vllm-metal через PydanticAI v2 (спека #18, раздел 5).

Модель выбрана подбором #70 (ADR-0009), до него была Qwen3.8-27B.

Агент задачи создаётся без модели: со своими инструкциями и `NativeOutput`. Модель ему отдаёт
`Llm.run`, и тот же вызов пишет запуск в ai_runs. Рассуждения по умолчанию выключены:
с `json_schema` они теряли данные или съедали лимит (ADR-0006). Задача включает их явно
(`thinking=True`) только в проверенном варианте: подбор #92, 5.4.
"""

import json
from collections.abc import Mapping, Sequence
from typing import Any, cast

import httpx2
from openai import APIError
from pydantic_ai import Agent
from pydantic_ai.exceptions import (
    FallbackExceptionGroup,
    ModelAPIError,
    ModelHTTPError,
    UnexpectedModelBehavior,
)
from pydantic_ai.messages import BinaryContent, UserContent
from pydantic_ai.models import Model
from pydantic_ai.models.fallback import FallbackModel
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIChatModelSettings
from pydantic_ai.providers.vllm import VLLMProvider
from pydantic_ai.settings import ModelSettings

from clin_compass.core.ai.nodes import (
    AiNoAnswerError,
    AiOutputError,
    AiServiceError,
    NodesUnavailableError,
    is_unavailable,
    node_urls,
    openai_client,
    report_unavailable,
)
from clin_compass.core.ai.runs import AiResult, RunRecorder, hash_input, track_run
from clin_compass.core.config import Settings

# Имя модели на узле: --served-model-name в services/llm/serve.sh (MODEL_ALIAS в versions.env).
MODEL_ALIAS = "qwen3.6-35b-a3b"
# Лимит ответа по умолчанию. Без него зациклившаяся модель писала бы до конца контекста
# (--max-model-len 16384): минуты работы узла и таймаут ответа. Ответы задач по схеме короче.
# Задача задаёт свой `max_tokens` в настройках агента или запуска.
DEFAULT_MAX_TOKENS = 4096
_DEFAULTS = OpenAIChatModelSettings(
    max_tokens=DEFAULT_MAX_TOKENS,
    extra_body={"chat_template_kwargs": {"enable_thinking": False}},
)


def build_llm_model(settings: Settings, *, model: str = MODEL_ALIAS) -> Model:
    """Модель из узлов `COMPASS_LLM_URLS`: недоступен узел — запрос уходит на следующий.

    Ошибки сервера и таймаут ответа клиент отдаёт сразу, без повторов: повторяет очередь.
    `model` — имя модели на узле; другое имя нужно только подбору моделей в eval (#70).

    Raises:
        AiNotConfiguredError: Адреса узлов LLM не заданы.
    """
    timeout = httpx2.Timeout(
        settings.llm_timeout_seconds, connect=settings.ai_connect_timeout_seconds
    )
    models = [
        OpenAIChatModel(
            model,
            provider=VLLMProvider(openai_client=openai_client(url, timeout)),
            settings=_DEFAULTS,
        )
        for url in node_urls(settings, "llm")
    ]
    return FallbackModel(*models, fallback_on=_switch_node)


def _switch_node(exc: Exception) -> bool:
    """Правило `is_unavailable` для `FallbackModel`; переход к следующему узлу пишется в лог.

    Адрес узла берём из запроса в ошибке OpenAI SDK: `FallbackModel` его не сообщает.
    """
    if not is_unavailable(exc):
        return False
    report_unavailable("llm", _request_node(exc), exc)
    return True


def _request_node(exc: BaseException) -> str | None:
    """Корень узла из запроса в цепочке `__cause__`: `http://<адрес>:<порт>`."""
    current: BaseException | None = exc
    while current is not None:
        if isinstance(current, APIError):
            url = current.request.url
            return f"{url.scheme}://{url.netloc.decode()}"
        current = current.__cause__
    return None


class Llm:
    """Запуск агентов задач на модели из узлов с записью каждого запуска в ai_runs."""

    def __init__(self, model: Model, recorder: RunRecorder) -> None:
        self._model = model
        self._recorder = recorder

    async def run[OutputT](
        self,
        agent: Agent[None, OutputT],
        prompt: str | Sequence[UserContent],
        *,
        task: str,
        prompt_version: str,
        model_settings: ModelSettings | None = None,
        thinking: bool = False,
    ) -> AiResult[OutputT]:
        """Запускает агента задачи и пишет запуск в ai_runs.

        Args:
            agent: Агент задачи без модели: инструкции и схема ответа.
            prompt: Вход задачи: текст или текст с изображениями.
            task: Имя задачи для ai_runs, например `extraction`.
            prompt_version: Версия промпта из репозитория.
            model_settings: Настройки генерации задачи. Перекрывают настройки агента,
                `extra_body` сливается с `extra_body` агента. `max_tokens` по умолчанию
                `DEFAULT_MAX_TOKENS`. В ai_runs пишется ровно то, что ушло на узел.
            thinking: Рассуждения модели (`enable_thinking`); по умолчанию выключены.

        Returns:
            Ответ по схеме агента и id строки ai_runs.

        Raises:
            NodesUnavailableError: Ни один узел LLM не принял запрос.
            AiServiceError: Узел ответил ошибкой HTTP. Тело ответа в ошибку не попадает:
                в нём узел может повторить вход.
            AiNoAnswerError: Узел принял запрос, но не ответил: таймаут ответа или обрыв.
            AiOutputError: Модель ответила не по схеме агента.
        """
        # Настройки агента-функции (`RunContext` → настройки) не сливаются: их значения
        # известны только во время запуска.
        agent_settings: Mapping[str, Any] = (
            agent.model_settings if isinstance(agent.model_settings, Mapping) else {}
        )
        effective = _run_settings(agent_settings, model_settings, thinking=thinking)
        # Запуск перекрывает агента: в ai_runs ровно то, что ушло на узел.
        params = {**agent_settings, **effective}
        async with track_run(
            self._recorder,
            role="llm",
            task=task,
            model=MODEL_ALIAS,
            input_hash=hash_input(_prompt_bytes(prompt)),
            params=_json_safe(params),
            prompt_version=prompt_version,
        ) as run:
            try:
                result = await agent.run(prompt, model=self._model, model_settings=effective)
            except FallbackExceptionGroup as exc:
                raise NodesUnavailableError("llm") from exc
            except ModelHTTPError as exc:
                raise AiServiceError("llm", exc.status_code) from None
            except ModelAPIError:
                # После ModelHTTPError: он подкласс ModelAPIError. Сюда попадают таймаут ответа
                # и обрыв соединения; отказ соединения ушёл в FallbackExceptionGroup.
                raise AiNoAnswerError("llm") from None
            except UnexpectedModelBehavior:
                # В цепочке причин — ошибка pydantic со значением из ответа модели.
                raise AiOutputError("llm") from None
            response = result.response
            run.model = response.model_name or MODEL_ALIAS
            run.node = _node_root(response.provider_url)
            usage = result.usage
            run.input_tokens = usage.input_tokens
            run.output_tokens = usage.output_tokens
        if run.run_id is None:
            # track_run выставляет id при выходе без ошибки; без него результат не отдаём.
            raise RuntimeError("track_run не выставил id строки ai_runs")
        return AiResult(output=result.output, run_id=run.run_id)


def _run_settings(
    agent_settings: Mapping[str, Any], settings: ModelSettings | None, *, thinking: bool = False
) -> ModelSettings:
    """Настройки запуска: лимит по умолчанию, `extra_body` агента и задачи, рассуждения.

    PydanticAI сливает настройки поверхностно (модель → агент → запуск): `extra_body` запуска
    целиком заменил бы `extra_body` агента и модели. Поэтому `extra_body` сливается здесь,
    а `enable_thinking` ставится поверх всего: его задаёт только `thinking`. Лимит по умолчанию
    ставится, только если его не задали ни агент, ни задача.
    """
    merged: dict[str, Any] = dict(settings or {})
    if "max_tokens" not in agent_settings:
        merged.setdefault("max_tokens", DEFAULT_MAX_TOKENS)
    extra = {
        **cast(Mapping[str, Any], agent_settings.get("extra_body") or {}),
        **cast(Mapping[str, Any], merged.get("extra_body") or {}),
    }
    template = dict(cast(Mapping[str, Any], extra.get("chat_template_kwargs") or {}))
    extra["chat_template_kwargs"] = {**template, "enable_thinking": thinking}
    merged["extra_body"] = extra
    return cast(ModelSettings, merged)


def _json_safe(params: Mapping[str, Any]) -> dict[str, Any]:
    """Параметры для JSONB: значение, которое не сериализуется (например, `Timeout`), — строкой.

    Иначе запись в ai_runs упала бы после успешного вызова, и задача повторила бы вызов LLM.
    """
    safe: dict[str, Any] = {}
    for key, value in params.items():
        try:
            json.dumps(value)
        except (TypeError, ValueError):
            safe[key] = str(value)
        else:
            safe[key] = value
    return safe


def _prompt_bytes(prompt: str | Sequence[UserContent]) -> bytes:
    """Вход для хеша: текст в UTF-8, у файлов — их байты."""
    if isinstance(prompt, str):
        return prompt.encode()
    parts = []
    for part in prompt:
        if isinstance(part, str):
            parts.append(part.encode())
        elif isinstance(part, BinaryContent):
            parts.append(part.data)
        else:
            parts.append(repr(part).encode())
    return b"\0".join(parts)


def _node_root(provider_url: str | None) -> str | None:
    """`http://<адрес>:8000/v1/` → `http://<адрес>:8000`, как в настройках."""
    if provider_url is None:
        return None
    return provider_url.rstrip("/").removesuffix("/v1")


__all__ = ["DEFAULT_MAX_TOKENS", "MODEL_ALIAS", "Llm", "build_llm_model"]
