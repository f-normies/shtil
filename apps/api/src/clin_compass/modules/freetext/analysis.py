"""Разбор текста без базы: два агента, проверка цитат, сверка с ГРЛС (спека #32, разделы 4.2–4.3).

Этим же кодом пользуется `eval/freetext`: оценка меряет то, что работает в worker'е.
Вид признака в ответе модели ограничен перечнем видов `reviewed` прямо в JSON-схеме:
vLLM не даст модели выдумать вид, а ответ не по схеме — `AiOutputError`.
"""

import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from string import Template
from typing import Any, Literal, cast
from uuid import UUID

import structlog
from clin_compass_knowledge.grls import DrugCheck, Registry, drug_name
from clin_compass_knowledge.schemas.sign import Sign
from clin_compass_knowledge.schemas.source import Source
from pydantic import BaseModel, Field, create_model
from pydantic_ai import Agent, NativeOutput
from pydantic_ai.settings import ModelSettings

from clin_compass.core.ai.llm import Llm
from clin_compass.modules.freetext.schemas import AdherenceEvent, FindingCategory, SourceType

log = structlog.get_logger()

PROMPTS = Path(__file__).parent / "prompts"
RISK_TASK = "freetext.check_risk"
EXTRACT_TASK = "freetext.extract"
# Ответ проверки риска — десятки токенов, полного разбора — сотни. Температура 0: один текст —
# один ответ, так eval воспроизводим.
_RISK_SETTINGS = ModelSettings(max_tokens=512, temperature=0)
# Полный разбор длинной расшифровки — десятки находок: лимит как у клиента по умолчанию.
_EXTRACT_SETTINGS = ModelSettings(max_tokens=4096, temperature=0)
_TEXT_KIND = {"diary": "запись в дневнике", "message": "сообщение врачу в чате"}
_TRANSCRIPT = (
    "расшифровка голосового сообщения ({kind}). Названия препаратов и редкие слова "
    "могли распознаться с ошибками"
)
_INPUT = "Вид текста: {kind}.\n\nТекст пациента:\n<текст>\n{text}\n</текст>"
# Кавычки и знаки, которыми модель обрамляет цитату. Внутри цитаты они остаются.
_QUOTE_EDGES = " \"'«»“”„.,;:!?…"


class SignOutput(BaseModel):
    """Признак риска в ответе модели. Поле `kind` сужается до видов `reviewed` при сборке."""

    kind: str
    quote: str


class FindingOutput(BaseModel):
    """Находка в ответе модели."""

    category: FindingCategory
    summary: str
    quote: str
    drug_name: str | None
    adherence_event: AdherenceEvent | None


class RiskCheckOutput(BaseModel):
    signs: list[SignOutput]


class ExtractionOutput(BaseModel):
    findings: list[FindingOutput]
    signs: list[SignOutput]


@dataclass(frozen=True)
class FoundSign:
    """Признак риска: вид, цитата и нашлась ли цитата в тексте дословно."""

    kind: str
    quote: str
    quote_found: bool


@dataclass(frozen=True)
class TextFinding:
    """Находка с проверенной цитатой и итогом сверки названия препарата."""

    category: FindingCategory
    summary: str
    quote: str
    drug: DrugCheck | None
    adherence_event: AdherenceEvent | None


@dataclass(frozen=True)
class RiskResult:
    signs: tuple[FoundSign, ...]
    run_id: UUID


@dataclass(frozen=True)
class ExtractResult:
    findings: tuple[TextFinding, ...]
    signs: tuple[FoundSign, ...]
    # Находки, цитату которых код не нашёл в тексте.
    dropped: int
    run_id: UUID


def quote_in_text(quote: str, text: str) -> bool:
    """Цитата есть в тексте целыми словами, без учёта регистра, пробелов, «ё/е» и кавычек по краям.

    «плохо» внутри «неплохо» не считается: иначе выдуманная цитата прошла бы проверку.
    """
    needle = _normalize(quote).strip(_QUOTE_EDGES)
    if not needle:
        return False
    return re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", _normalize(text)) is not None


def _normalize(value: str) -> str:
    return " ".join(value.lower().replace("ё", "е").split())


class Analyzer:
    """Агенты проверки риска и полного разбора для видов признаков `reviewed`."""

    def __init__(
        self,
        llm: Llm,
        signs: Iterable[Sign],
        registry: Registry,
        sources: Iterable[Source] = (),
    ) -> None:
        reviewed = sorted((s for s in signs if s.status == "reviewed"), key=lambda s: s.rank)
        if not reviewed:
            raise ValueError("нет видов признаков риска со статусом reviewed")
        self.signs = {s.id: s for s in reviewed}
        # Манифест источников: название и адрес документа попадают в сигнал (#13).
        self.sources = {s.id: s for s in sources}
        self._llm = llm
        self._registry = registry
        risk_output, extract_output = _output_models([s.id for s in reviewed])
        listed = "\n".join(
            f"- `{s.id}` — {s.title}. {' '.join(s.definition.split())}" for s in reviewed
        )
        risk = _render("risk.md", listed)
        extract = _render("extract.md", listed)
        self.risk_prompt_version = _version("risk", risk, risk_output)
        self.extract_prompt_version = _version("extract", extract, extract_output)
        self._risk_agent: Agent[None, RiskCheckOutput] = Agent(
            output_type=NativeOutput(risk_output), instructions=risk, model_settings=_RISK_SETTINGS
        )
        self._extract_agent: Agent[None, ExtractionOutput] = Agent(
            output_type=NativeOutput(extract_output),
            instructions=extract,
            model_settings=_EXTRACT_SETTINGS,
        )

    async def check_risk(
        self, text: str, *, source_type: SourceType, transcript: bool
    ) -> RiskResult:
        """Быстрая проверка: есть ли признаки риска, с цитатами."""
        result = await self._llm.run(
            self._risk_agent,
            _prompt(text, source_type, transcript),
            task=RISK_TASK,
            prompt_version=self.risk_prompt_version,
        )
        return RiskResult(_signs(result.output.signs, text), result.run_id)

    async def extract(
        self, text: str, *, source_type: SourceType, transcript: bool
    ) -> ExtractResult:
        """Полный разбор: находки четырёх категорий и признаки риска."""
        result = await self._llm.run(
            self._extract_agent,
            _prompt(text, source_type, transcript),
            task=EXTRACT_TASK,
            prompt_version=self.extract_prompt_version,
        )
        kept = [f for f in result.output.findings if quote_in_text(f.quote, text)]
        dropped = len(result.output.findings) - len(kept)
        if dropped:
            # Только число: в цитате медицинский текст.
            log.info("freetext.quotes_not_found", dropped=dropped, run_id=str(result.run_id))
        findings = tuple(self._finding(f) for f in kept)
        return ExtractResult(findings, _signs(result.output.signs, text), dropped, result.run_id)

    def _finding(self, found: FindingOutput) -> TextFinding:
        # «Пропустила таблетку»: общее слово — не название, сверять нечего.
        name = (found.drug_name or "").strip() if drug_name(found.drug_name or "") else ""
        return TextFinding(
            category=found.category,
            summary=found.summary,
            quote=found.quote,
            drug=self._registry.lookup(name) if name else None,
            adherence_event=found.adherence_event if found.category == "adherence" else None,
        )


def _signs(signs: list[SignOutput], text: str) -> tuple[FoundSign, ...]:
    return tuple(FoundSign(s.kind, s.quote, quote_in_text(s.quote, text)) for s in signs)


def _output_models(
    sign_ids: list[str],
) -> tuple[type[RiskCheckOutput], type[ExtractionOutput]]:
    """Схемы ответа, где `kind` — перечень видов `reviewed`."""
    kind: Any = cast(Any, Literal)[tuple(sign_ids)]
    sign = create_model(
        "RiskSign",
        __base__=SignOutput,
        kind=(kind, Field(description="id вида признака из списка")),
    )
    risk = create_model("RiskCheck", __base__=RiskCheckOutput, signs=(list[sign], ...))
    extract = create_model("Extraction", __base__=ExtractionOutput, signs=(list[sign], ...))
    return risk, extract


def _render(name: str, signs: str) -> str:
    return Template((PROMPTS / name).read_text(encoding="utf-8")).substitute(signs=signs)


def _version(task: str, instructions: str, output: type[BaseModel]) -> str:
    """Версия промпта: задача и 8 знаков SHA-256 всего, что видит модель, кроме самого текста.

    Инструкции с определениями видов, обёртка входа и схема ответа: правка любого из них
    меняет версию сама.
    """
    seen = json.dumps(
        {
            "instructions": instructions,
            "input": [_INPUT, _TRANSCRIPT, _TEXT_KIND],
            "schema": output.model_json_schema(),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return f"{task}-{hashlib.sha256(seen.encode()).hexdigest()[:8]}"


def _prompt(text: str, source_type: SourceType, transcript: bool) -> str:
    kind = _TEXT_KIND[source_type]
    if transcript:
        kind = _TRANSCRIPT.format(kind=kind)
    return _INPUT.format(kind=kind, text=text)
