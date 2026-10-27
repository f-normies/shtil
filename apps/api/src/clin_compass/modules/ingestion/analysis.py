"""Извлечение фактов из текста документа без базы (спека #30, разделы 3.4 и 5).

Этим же кодом пользуется `eval/extraction`: оценка меряет то, что работает в worker'е.
Модель отвечает по JSON-схеме (`NativeOutput`). Код проверяет ответ: цитата каждого факта
есть в тексте, код МКБ-10 — по формату, название из текста и дата отмены — в цитате, дата
документа — в тексте. Пустые факты и повторы отбрасываются. Название препарата сверяется
со снимком ГРЛС.
"""

import hashlib
import json
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal
from uuid import UUID

import structlog
from clin_compass_knowledge.grls import DrugCheck, Registry, drug_name, normalize
from pydantic import BaseModel, Field
from pydantic_ai import Agent, NativeOutput
from pydantic_ai.settings import ModelSettings

from clin_compass.core.ai.llm import Llm
from clin_compass.modules.ingestion.dates import date_in_quote, date_in_text, days_before, parse_iso
from clin_compass.modules.ingestion.document import quote_found
from clin_compass.modules.ingestion.schemas import MedicationStatus, StopDateSource

log = structlog.get_logger()

PROMPTS = Path(__file__).parent / "prompts"
EXTRACT_TASK = "ingestion.extract"
# Самая длинная история #9 — около 20 фактов; ответ на 15 000 знаков текста — около 5 тыс.
# токенов. Температура 0: один текст — один ответ, так eval воспроизводим.
_SETTINGS = ModelSettings(max_tokens=6144, temperature=0)
_LAYER_KIND = "PDF с текстовым слоем"
_OCR_KIND = "текст распознан с изображения, названия и цифры могли исказиться"
_INPUT = "Вид документа: {kind}.\n\nТекст истории болезни:\n<текст>\n{text}\n</текст>"
# Тег внутри документа не должен закрыть данные раньше времени: он становится «‹текст›».
_TAGS = re.compile(r"<(/?)\s*текст\s*>", re.IGNORECASE)
_ICD10 = re.compile(r"[A-Z]\d{2}(?:\.\d{1,2})?")
# Кириллические буквы, похожие на латинские: «Е11.9» в тексте или ответе — это E11.9.
_LATIN = str.maketrans("АВЕКМНОРСТХ", "ABEKMHOPCTX")
_CYRILLIC = re.compile(r"[а-яё]", re.IGNORECASE)


class DiagnosisOutput(BaseModel):
    icd10: str = Field(description="код МКБ-10 латиницей, например F33.1")
    code_in_text: bool = Field(description="этот код написан в тексте")
    text: str
    quote: str


class MedicationOutput(BaseModel):
    name: str = Field(description="русское название в начальной форме, как в ГРЛС")
    name_in_text: str = Field(description="название, как оно написано в тексте")
    dose: str | None
    regimen: str | None
    status: MedicationStatus
    stopped_on: str | None = Field(description="дата отмены ГГГГ-ММ-ДД или null")
    stopped_days_before_document: int | None = Field(
        description="за сколько дней до даты документа отменён, если так и написано, иначе null"
    )
    quote: str


class FactOutput(BaseModel):
    fact: str
    quote: str


class ExtractionOutput(BaseModel):
    document_date: str | None = Field(description="дата документа ГГГГ-ММ-ДД или null")
    diagnoses: list[DiagnosisOutput]
    medications: list[MedicationOutput]
    anamnesis: list[FactOutput]
    risk_factors: list[FactOutput]


@dataclass(frozen=True)
class FoundDiagnosis:
    icd10: str
    text: str
    quote: str
    # Код написан в тексте; иначе его предложила модель.
    code_in_text: bool


@dataclass(frozen=True)
class FoundMedication:
    """Препарат: название после модели, как в тексте, и итог сверки с ГРЛС."""

    name: str
    name_in_text: str
    renamed: bool
    dose: str | None
    regimen: str | None
    status: MedicationStatus
    stopped_on: date | None
    # Дата отмены написана в цитате или вычислена от даты документа.
    stopped_on_source: StopDateSource | None
    quote: str
    drug: DrugCheck


FactField = Literal["anamnesis", "risk_factor"]


@dataclass(frozen=True)
class FoundFact:
    field: FactField
    fact: str
    quote: str


@dataclass(frozen=True)
class Extraction:
    document_date: date | None
    diagnoses: tuple[FoundDiagnosis, ...]
    medications: tuple[FoundMedication, ...]
    facts: tuple[FoundFact, ...]
    # Факты, отброшенные проверкой: цитаты нет, код не по формату, пустой факт, повтор.
    dropped: int
    run_id: UUID


class Extractor:
    """Агент извлечения фактов из истории болезни и проверки его ответа."""

    def __init__(self, llm: Llm, registry: Registry) -> None:
        self._llm = llm
        self._registry = registry
        instructions = (PROMPTS / "extract.md").read_text(encoding="utf-8")
        output = ExtractionOutput
        self.prompt_version = _version(instructions, output)
        self._agent: Agent[None, ExtractionOutput] = Agent(
            output_type=NativeOutput(output), instructions=instructions, model_settings=_SETTINGS
        )

    async def extract(self, text: str, *, ocr: bool) -> Extraction:
        """Факты текста с проверенными цитатами и кодами. `ocr` — текст распознан с картинки."""
        kind = _OCR_KIND if ocr else _LAYER_KIND
        result = await self._llm.run(
            self._agent,
            _INPUT.format(kind=kind, text=_TAGS.sub(r"‹\1текст›", text)),
            task=EXTRACT_TASK,
            prompt_version=self.prompt_version,
        )
        output = result.output
        document_date = parse_iso(output.document_date)
        if document_date is not None and not date_in_text(document_date, text):
            document_date = None
        diagnoses = _unique(
            (d for d in (_diagnosis(found, text) for found in output.diagnoses) if d),
            key=lambda d: d.icd10,
        )
        medications = _unique(
            (
                self._medication(m, document_date)
                for m in output.medications
                if quote_found(m.quote, text) and drug_name(m.name).strip()
            ),
            key=lambda m: name_key(m.name),
        )
        groups: tuple[tuple[FactField, list[FactOutput]], ...] = (
            ("anamnesis", output.anamnesis),
            ("risk_factor", output.risk_factors),
        )
        facts = [
            FoundFact(field, f.fact.strip(), f.quote)
            for field, found in groups
            for f in found
            if f.fact.strip() and quote_found(f.quote, text)
        ]
        answered = (
            len(output.diagnoses)
            + len(output.medications)
            + len(output.anamnesis)
            + len(output.risk_factors)
        )
        dropped = answered - len(diagnoses) - len(medications) - len(facts)
        if dropped:
            # Только число: в цитате медицинский текст.
            log.info("ingestion.facts_dropped", dropped=dropped, run_id=str(result.run_id))
        return Extraction(
            document_date=document_date,
            diagnoses=tuple(diagnoses),
            medications=tuple(medications),
            facts=tuple(facts),
            dropped=dropped,
            run_id=result.run_id,
        )

    def _medication(self, found: MedicationOutput, document_date: date | None) -> FoundMedication:
        name = drug_name(found.name).strip()
        drug = self._registry.lookup(name)
        written = drug_name(found.name_in_text).strip()
        if drug.check == "not_found" and _CYRILLIC.search(written) and _renamed(name, written):
            # Модель заменила кириллическое название на то, которого нет в ГРЛС: Qwen3.6
            # не знает «Авиандр» и выдумывает МНН. В карточку идёт название из текста.
            name, drug = written, self._registry.lookup(written)
        stopped_on, source = _stop_date(found, document_date)
        return FoundMedication(
            name=name,
            name_in_text=found.name_in_text.strip(),
            # Названия из текста нет в цитате — модель его изменила: врач сверит с цитатой.
            renamed=_renamed(name, found.name_in_text)
            or not quote_found(found.name_in_text, found.quote),
            dose=_optional(found.dose),
            regimen=_optional(found.regimen),
            status=found.status,
            stopped_on=stopped_on,
            stopped_on_source=source,
            quote=found.quote,
            drug=drug,
        )


def name_key(name: str) -> str:
    """Ключ сравнения названий препаратов: без дозы и формы, нормализация ГРЛС."""
    return normalize(drug_name(name)) or normalize(name)


def _unique[T](items: Iterable[T], key: Callable[[T], str]) -> list[T]:
    """Первый факт с каждым ключом: повтор в одном документе — один факт."""
    seen: set[str] = set()
    kept: list[T] = []
    for item in items:
        if key(item) not in seen:
            seen.add(key(item))
            kept.append(item)
    return kept


def _stop_date(
    found: MedicationOutput, document_date: date | None
) -> tuple[date | None, StopDateSource | None]:
    """Дата отмены: написана в цитате или вычислена от даты документа по интервалу из цитаты."""
    if found.status == "current":
        return None, None
    written = parse_iso(found.stopped_on)
    if written is not None and date_in_quote(written, found.quote):
        return written, "text"
    computed = days_before(document_date, found.stopped_days_before_document, found.quote)
    return (computed, "computed") if computed is not None else (None, None)


def _diagnosis(found: DiagnosisOutput, text: str) -> FoundDiagnosis | None:
    """Диагноз с кодом по формату и найденной цитатой; иначе None."""
    code = "".join(found.icd10.split()).upper().translate(_LATIN)
    if not _ICD10.fullmatch(code) or not found.text.strip() or not quote_found(found.quote, text):
        return None
    return FoundDiagnosis(
        icd10=code,
        text=found.text.strip(),
        quote=found.quote,
        code_in_text=found.code_in_text and _code_in(code, text),
    )


def _code_in(code: str, text: str) -> bool:
    """Код есть в тексте целиком: F33 не находится внутри F33.1, F33.1 — внутри F33.11."""
    pattern = rf"(?<![A-Z0-9.]){re.escape(code)}(?!\.?\d)"
    return re.search(pattern, text.upper().translate(_LATIN)) is not None


def _renamed(name: str, name_in_text: str) -> bool:
    """Модель изменила название из текста: латиница, опечатка, МНН вместо торгового.

    Ключи сравнения — нормализация ГРЛС. Последняя буква более короткого названия
    не сравнивается, остальное окончание длинного — тоже: падеж «пароксетином» —
    это «пароксетин», а не переименование.
    """
    a, b = normalize(drug_name(name)), normalize(drug_name(name_in_text))
    if not a or not b:
        return False
    short, long = sorted((a, b), key=len)
    stem = short[:-1] if len(short) > 4 else short
    return not long.startswith(stem)


def _optional(value: str | None) -> str | None:
    return value.strip() or None if value is not None else None


def _version(instructions: str, output: type[BaseModel]) -> str:
    """Версия промпта: 8 знаков SHA-256 инструкций, обёртки входа и схемы ответа.

    Правка любого из них меняет версию сама, как у `freetext.analysis._version`.
    """
    seen = json.dumps(
        {
            "instructions": instructions,
            "input": [_INPUT, _LAYER_KIND, _OCR_KIND],
            "schema": output.model_json_schema(),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return f"extract-{hashlib.sha256(seen.encode()).hexdigest()[:8]}"
