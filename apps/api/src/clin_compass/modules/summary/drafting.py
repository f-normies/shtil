"""Черновик сводки: вызов модели и проверка утверждений кодом (спека #38, 3.3).

Модель видит только пронумерованные факты (`facts.collect`). Каждое утверждение проверяет
`verification.verify`. Если не осталось ни одного утверждения — это сбой ответа.
"""

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID
from zoneinfo import ZoneInfo

import structlog
from pydantic import BaseModel
from pydantic_ai import Agent, NativeOutput
from pydantic_ai.settings import ModelSettings

from clin_compass.core.ai.llm import Llm
from clin_compass.modules.summary.schemas import SummaryFact, SummaryStatement
from clin_compass.modules.summary.verification import StatementOutput, verify

log = structlog.get_logger()

TASK = "summary.draft"
PROMPT = Path(__file__).parent / "prompts" / "summary.md"
# Пять коротких утверждений — сотни токенов. Температура 0: одни факты — один ответ.
_SETTINGS = ModelSettings(max_tokens=1024, temperature=0)
_INPUT = "Факты о пациенте с прошлого приёма:\n\n{facts}"
_FACT = "[{number}] {text} Дата: {day}."

__all__ = ["TASK", "Draft", "Drafter", "StatementsRejectedError", "render"]


class DraftOutput(BaseModel):
    """Ответ модели: утверждения сводки."""

    statements: list[StatementOutput]


@dataclass(frozen=True)
class Draft:
    """Проверенные утверждения, число отброшенных и строка ai_runs вызова."""

    statements: tuple[SummaryStatement, ...]
    dropped: int
    run_id: UUID


class StatementsRejectedError(RuntimeError):
    """Проверка не оставила ни одного утверждения: сводки нет, задача считает это сбоем."""


class Drafter:
    """Агент сводки на модели из узлов; каждый вызов пишется в ai_runs задачей `summary.draft`."""

    def __init__(self, llm: Llm, zone: ZoneInfo) -> None:
        instructions = PROMPT.read_text(encoding="utf-8")
        self.prompt_version = _version(instructions)
        self._llm = llm
        self._zone = zone
        self._agent: Agent[None, DraftOutput] = Agent(
            output_type=NativeOutput(DraftOutput),
            instructions=instructions,
            model_settings=_SETTINGS,
        )

    async def draft(self, facts: Sequence[SummaryFact]) -> Draft:
        """Утверждения модели по фактам после проверки кодом.

        Raises:
            StatementsRejectedError: Ни одно утверждение не прошло проверку.
            AiError: Ошибки клиента LLM (`core.ai.llm.Llm.run`).
        """
        result = await self._llm.run(
            self._agent,
            render(facts, self._zone),
            task=TASK,
            prompt_version=self.prompt_version,
        )
        kept, dropped = verify(result.output.statements, facts, self._zone)
        if dropped:
            # Только число: в тексте утверждения — данные пациента.
            log.info("summary.statements_dropped", dropped=dropped, run_id=str(result.run_id))
        if not kept:
            raise StatementsRejectedError
        return Draft(tuple(kept), dropped, result.run_id)


def render(facts: Sequence[SummaryFact], zone: ZoneInfo) -> str:
    """Вход модели: факты по номерам, у каждого дата по местному времени."""
    lines = [
        _FACT.format(number=f.number, text=f.text, day=f.at.astimezone(zone).strftime("%d.%m"))
        for f in facts
    ]
    return _INPUT.format(facts="\n".join(lines))


def _version(instructions: str) -> str:
    """Версия промпта: задача и 8 знаков SHA-256 инструкций, обёртки входа и схемы ответа."""
    seen = json.dumps(
        {
            "instructions": instructions,
            "input": [_INPUT, _FACT],
            "schema": DraftOutput.model_json_schema(),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return f"summary-{hashlib.sha256(seen.encode()).hexdigest()[:8]}"
