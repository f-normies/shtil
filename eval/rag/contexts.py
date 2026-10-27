"""Строка контекста к фрагменту для поиска (спека #70, 5.3; contextual retrieval).

LLM пишет одно-два предложения: о чём фрагмент и где он в документе. Строка ставится
перед текстом в эмбеддинге и полнотекстовом индексе. Ответ модели и цитаты её не видят.
"""

import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path

from clin_compass.core.ai.llm import Llm
from pydantic import BaseModel
from pydantic_ai import Agent, NativeOutput
from pydantic_ai.settings import ModelSettings

from retrieval import Doc

PROMPT = Path(__file__).resolve().parent / "prompts" / "context.md"
TASK = "eval.rag.context"
_INPUT = (
    "Документ: {title}\nРазделы документа:\n{headings}\n\nПуть заголовков фрагмента: {heading}\n"
    "<фрагмент>\n{text}\n</фрагмент>"
)
_SETTINGS = ModelSettings(max_tokens=200, temperature=0)


class ContextOutput(BaseModel):
    context: str


def section_headings(docs: Mapping[str, Doc]) -> dict[str, list[str]]:
    """Источник → пути заголовков его фрагментов без повторов, в порядке ключей."""
    result: dict[str, list[str]] = {}
    for key in sorted(docs):
        doc = docs[key]
        headings = result.setdefault(doc.source_id, [])
        if doc.heading_path not in headings:
            headings.append(doc.heading_path)
    return result


class Contextualizer:
    """Агент, который пишет строку контекста к фрагменту."""

    def __init__(self, llm: Llm) -> None:
        self._llm = llm
        instructions = PROMPT.read_text(encoding="utf-8")
        self._agent: Agent[None, ContextOutput] = Agent(
            output_type=NativeOutput(ContextOutput),
            instructions=instructions,
            model_settings=_SETTINGS,
        )
        seen = json.dumps([instructions, _INPUT], ensure_ascii=False)
        self.prompt_version = f"{TASK}-{hashlib.sha256(seen.encode()).hexdigest()[:8]}"

    async def context(self, doc: Doc, headings: Sequence[str]) -> str:
        """Одно-два предложения о фрагменте."""
        prompt = _INPUT.format(
            title=doc.title,
            headings="\n".join(f"- {h}" for h in headings),
            heading=doc.heading_path,
            text=doc.text,
        )
        result = await self._llm.run(
            self._agent, prompt, task=TASK, prompt_version=self.prompt_version
        )
        return result.output.context.strip()
