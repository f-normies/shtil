"""Ответ по найденным фрагментам с проверкой цитат (спека #27, раздел 7).

Метка фрагмента в ответе модели ограничена метками из промпта прямо в JSON-схеме: vLLM
не даст модели сослаться на фрагмент, которого она не видела.

Два режима цитаты (спека #70, 6.1). `quote` — модель переписывает цитату дословно, код ищет
её во фрагменте. `sentences` — текст фрагмента в промпте разбит на пронумерованные части,
модель указывает диапазон номеров, код вырезает цитату сам: она дословна по построению.

Варианты подбора #92 (спека #92, 5.3–5.4): `premise` добавляет к инструкциям правила для
вопросов с неверным условием и неполным ответом (`prompts/answer_premise.md`), `thinking`
включает рассуждения модели всегда или только при сведениях о пациенте. Вариант #105
(спека #105, 5.3): `aspects` — перед утверждениями модель перечисляет части вопроса и отвечает
на каждую (`prompts/answer_aspects.md`); части остаются внутри модуля.

Цитата должна подтверждать утверждение: не короче трёх слов, все числа утверждения
есть в цитате. Иначе врач утверждение не видит, оно считается в `dropped`. Врач видит цитату
так, как она написана во фрагменте.
"""

import hashlib
import json
import re
from collections.abc import Sequence
from pathlib import Path
from string import Template
from typing import Any, Literal, cast

import structlog
from pydantic import BaseModel, Field, create_model
from pydantic_ai import Agent, NativeOutput
from pydantic_ai.settings import ModelSettings

from clin_compass.core.ai.llm import Llm
from clin_compass.modules.knowledge.schemas import Answer, Claim, Fragment, FragmentRef, Hit
from clin_compass.modules.knowledge.sentences import numbered, quote_span, units

log = structlog.get_logger()

PROMPTS = Path(__file__).parent / "prompts"
TASK = "knowledge.answer"
# Боевой вариант ответа — по подбору #70 (ADR-0009): номера частей, 5 фрагментов, 5 утверждений.
# Он равен по качеству цитате и 8 фрагментам, но быстрее и чаще отказывается без ответа в базе.
# Сколько фрагментов видит модель: столько отдаёт поиск ответа.
MAX_FRAGMENTS = 5
MAX_CLAIMS = 5
# Суммарный текст фрагментов в промпте: около 6 тыс. токенов при окне LLM 16 тыс.
CONTEXT_BUDGET = 24_000
# Цитата короче — одно-два слова, она не подтверждает утверждение.
MIN_QUOTE_WORDS = 3
# Режим `sentences`: цитата длиннее четырёх частей — уже не цитата, а пересказ раздела.
MAX_SPAN_UNITS = 4
Citation = Literal["quote", "sentences"]
CITATION: Citation = "sentences"
Thinking = Literal["off", "always", "context"]
# С рассуждениями ответ длиннее: рассуждения идут в тот же лимит токенов.
THINKING_MAX_TOKENS = 8192
_PROMPT_FILES: dict[str, str] = {"quote": "answer.md", "sentences": "answer_sentences.md"}
_PREMISE_FILE = "answer_premise.md"
_ASPECTS_FILE = "answer_aspects.md"
MAX_ASPECTS = 5
# Температура 0: один вопрос — один ответ, так eval воспроизводим.
_SETTINGS = ModelSettings(max_tokens=4096, temperature=0)
_INPUT = "{context}Вопрос врача:\n<вопрос>\n{question}\n</вопрос>\n\nФрагменты:\n\n{fragments}"
_CONTEXT = "Сведения о пациенте:\n<пациент>\n{context}\n</пациент>\n\n"
_FRAGMENT = "[{label}] {title}, версия {version}. Раздел: {section}.{levels}\n{text}"
_LEVELS = " УУР {uur}, УДД {udd}."


class ClaimOutput(BaseModel):
    """Утверждение в ответе модели. Поле `fragment` сужается до меток промпта при сборке."""

    statement: str
    fragment: str
    quote: str


class UnitClaimOutput(BaseModel):
    """Утверждение в режиме `sentences`: диапазон номеров частей фрагмента вместо цитаты."""

    statement: str
    fragment: str
    first: int = Field(ge=1)
    last: int = Field(ge=1)


class AnswerOutput(BaseModel):
    claims: list[Any]


class AspectsAnswerOutput(BaseModel):
    """Ответ с частями вопроса. Не наследник `AnswerOutput`: иначе `claims` шли бы в схеме
    первыми, а модель должна сначала назвать части вопроса."""

    aspects: list[str]
    claims: list[Any]


class Answerer:
    """Агенты ответа на 1…`max_fragments` фрагментов: у каждого свой перечень меток."""

    def __init__(
        self,
        llm: Llm,
        *,
        citation: Citation = CITATION,
        max_fragments: int = MAX_FRAGMENTS,
        max_claims: int = MAX_CLAIMS,
        premise: bool = False,
        thinking: Thinking = "off",
        aspects: bool = False,
    ) -> None:
        self._llm = llm
        self.citation = citation
        self.max_fragments = max_fragments
        self.thinking = thinking
        text = (PROMPTS / _PROMPT_FILES[citation]).read_text(encoding="utf-8")
        if premise:
            text += (PROMPTS / _PREMISE_FILE).read_text(encoding="utf-8")
        if aspects:
            text += (PROMPTS / _ASPECTS_FILE).read_text(encoding="utf-8")
        self.instructions = Template(text).substitute(max_claims=max_claims)
        outputs = {
            count: _output_model(count, citation, max_claims, aspects=aspects)
            for count in range(1, max_fragments + 1)
        }
        # Версия считается по схеме с наибольшим числом меток: число найденных фрагментов
        # не меняет версию промпта.
        self.prompt_version = _version(self.instructions, outputs[max_fragments])
        self._agents: dict[int, Agent[None, AnswerOutput | AspectsAnswerOutput]] = {
            count: Agent(
                output_type=NativeOutput(output),
                instructions=self.instructions,
                model_settings=_SETTINGS,
            )
            for count, output in outputs.items()
        }

    async def answer(
        self, question: str, hits: Sequence[Hit], *, context: str | None = None
    ) -> Answer:
        """Ответ модели по фрагментам поиска; утверждения без подтверждающей цитаты отброшены.

        Нет фрагментов — пустой ответ без вызова модели (`run_id` None).

        Raises:
            NodesUnavailableError: Ни один узел LLM не принял запрос.
            AiServiceError, AiNoAnswerError: Узел ответил ошибкой или не ответил.
            AiOutputError: Модель ответила не по схеме.
        """
        shown = within_budget(hits, self.max_fragments)
        if not shown:
            return Answer(claims=[], dropped=0, run_id=None)
        thinking = self.thinking == "always" or (self.thinking == "context" and bool(context))
        result = await self._llm.run(
            self._agents[len(shown)],
            _prompt(question, shown, context, self.citation),
            task=TASK,
            prompt_version=self.prompt_version,
            model_settings=ModelSettings(max_tokens=THINKING_MAX_TOKENS) if thinking else None,
            thinking=thinking,
        )
        by_label = {_label(n): hit.fragment for n, hit in enumerate(shown, start=1)}
        claims = []
        for found in result.output.claims:
            fragment = by_label[found.fragment]
            quote = self._quote(found, fragment.text)
            if quote is not None and supports(found.statement, quote):
                claims.append(
                    Claim(statement=found.statement, quote=quote, fragment=_ref(fragment))
                )
        dropped = len(result.output.claims) - len(claims)
        if dropped:
            # Только число: в цитате может быть текст вопроса с данными пациента.
            log.info("knowledge.quotes_not_found", dropped=dropped, run_id=str(result.run_id))
        return Answer(claims=claims, dropped=dropped, run_id=result.run_id)

    def _quote(self, found: Any, text: str) -> str | None:
        if self.citation == "quote":
            return find_quote(found.quote, text)
        if found.last - found.first + 1 > MAX_SPAN_UNITS:
            return None
        return quote_span(text, units(text), found.first, found.last)


def within_budget(hits: Sequence[Hit], max_fragments: int = MAX_FRAGMENTS) -> list[Hit]:
    """Фрагменты по рангу, пока их текст умещается в `CONTEXT_BUDGET`; первый — всегда."""
    shown: list[Hit] = []
    total = 0
    for hit in hits[:max_fragments]:
        size = len(hit.fragment.text)
        if shown and total + size > CONTEXT_BUDGET:
            break
        shown.append(hit)
        total += size
    return shown


# Сравнение то же, что у `quote_in_text` в `clin_compass_knowledge.checks` (PR #65, разбор
# текста #32): целыми словами, без учёта регистра, пробелов, «ё/е» и кавычек по краям.
# Там ответ только «да/нет», здесь нужен сам участок текста фрагмента.
_QUOTE_EDGES = " \"'«»“”„.,;:!?…"
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")


def find_quote(quote: str, text: str) -> str | None:
    """Участок `text`, совпавший с цитатой, в написании текста; не найден — None.

    «плохо» внутри «неплохо» не считается: иначе выдуманная цитата прошла бы проверку.
    Кавычки и знаки препинания по краям цитаты в участок не входят.
    """
    needle = _normalize(quote).strip(_QUOTE_EDGES)
    if not needle:
        return None
    normalized, origins = _normalize_with_origins(text)
    match = re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", normalized)
    if match is None:
        return None
    return text[origins[match.start()] : origins[match.end() - 1] + 1]


def supports(statement: str, quote: str) -> bool:
    """Цитата подтверждает утверждение: в ней не меньше трёх слов и есть все числа утверждения.

    Числа сравниваются без учёта разделителя дробной части: 0,5 и 0.5 — одно число.
    """
    if len(re.findall(r"\w+", quote)) < MIN_QUOTE_WORDS:
        return False
    return _numbers(statement) <= _numbers(quote)


def _numbers(text: str) -> set[str]:
    return {number.replace(",", ".") for number in _NUMBER.findall(text)}


def _normalize(value: str) -> str:
    return " ".join(value.lower().replace("ё", "е").split())


def _normalize_with_origins(text: str) -> tuple[str, list[int]]:
    """`_normalize(text)` и для каждого его знака — позиция знака в исходном тексте."""
    chars: list[str] = []
    origins: list[int] = []
    for index, char in enumerate(text):
        if char.isspace():
            if chars and chars[-1] != " ":
                chars.append(" ")
                origins.append(index)
            continue
        for lowered in char.lower().replace("ё", "е"):
            chars.append(lowered)
            origins.append(index)
    if chars and chars[-1] == " ":
        chars.pop()
        origins.pop()
    return "".join(chars), origins


def _label(number: int) -> str:
    return f"F{number}"


def _output_model(
    count: int, citation: Citation, max_claims: int, *, aspects: bool = False
) -> type[AnswerOutput] | type[AspectsAnswerOutput]:
    """Схема ответа: `fragment` — перечень меток F1…F{count}, утверждений не больше `max_claims`.

    С `aspects` перед утверждениями — от 1 до `MAX_ASPECTS` частей вопроса.
    """
    labels: Any = cast(Any, Literal)[tuple(_label(n) for n in range(1, count + 1))]
    claim = create_model(
        "AnswerClaim",
        __base__=ClaimOutput if citation == "quote" else UnitClaimOutput,
        fragment=(labels, Field(description="метка фрагмента, на котором основано утверждение")),
    )
    if aspects:
        return create_model(
            "AnswerClaims",
            __base__=AspectsAnswerOutput,
            aspects=(list[str], Field(min_length=1, max_length=MAX_ASPECTS)),
            claims=(list[claim], Field(max_length=max_claims)),
        )
    return create_model(
        "AnswerClaims",
        __base__=AnswerOutput,
        claims=(list[claim], Field(max_length=max_claims)),
    )


def _version(instructions: str, output: type[BaseModel]) -> str:
    """Задача и 8 знаков SHA-256 инструкций, обёртки входа и схемы ответа.

    Правка любого из них меняет версию сама.
    """
    seen = json.dumps(
        {
            "instructions": instructions,
            "input": [_INPUT, _CONTEXT, _FRAGMENT, _LEVELS, CONTEXT_BUDGET],
            "schema": output.model_json_schema(),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return f"{TASK}-{hashlib.sha256(seen.encode()).hexdigest()[:8]}"


def _prompt(question: str, hits: Sequence[Hit], context: str | None, citation: Citation) -> str:
    fragments = "\n\n".join(
        _FRAGMENT.format(
            label=_label(n),
            title=hit.fragment.document.title,
            version=hit.fragment.document.version,
            section=_section(hit),
            levels=_LEVELS.format(uur=hit.fragment.uur, udd=hit.fragment.udd)
            if hit.fragment.uur is not None
            else "",
            text=numbered(hit.fragment.text) if citation == "sentences" else hit.fragment.text,
        )
        for n, hit in enumerate(hits, start=1)
    )
    return _INPUT.format(
        context=_CONTEXT.format(context=context) if context else "",
        question=question,
        fragments=fragments,
    )


def _section(hit: Hit) -> str:
    """Путь заголовков; если в нём нет подписи раздела, подпись идёт первой."""
    fragment = hit.fragment
    if fragment.section_title in fragment.heading_path:
        return fragment.heading_path
    return " > ".join(p for p in (fragment.section_title, fragment.heading_path) if p)


def _ref(fragment: Fragment) -> FragmentRef:
    return FragmentRef(
        id=fragment.id,
        key=fragment.key,
        source_id=fragment.document.source_id,
        title=fragment.document.title,
        version=fragment.document.version,
        section=fragment.section,
        section_title=fragment.section_title,
        uur=fragment.uur,
        udd=fragment.udd,
        status=fragment.document.status,
    )
