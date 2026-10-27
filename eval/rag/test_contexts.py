"""Строка контекста фрагмента (спека #70, 5.3). LLM не вызывается: `FunctionModel`."""

import json

import pytest
from clin_compass.core.ai.llm import MODEL_ALIAS, Llm
from clin_compass.core.ai.runs import NullRecorder
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from contexts import Contextualizer, section_headings
from retrieval import Doc


def _doc(key: str, heading: str, source: str = "kr-gad-2024") -> Doc:
    return Doc(key=key, source_id=source, title="КР ГТР", heading_path=heading, text="Текст.")


def test_section_headings_are_unique_per_document_in_key_order() -> None:
    docs = {
        "kr-gad-2024/doc_2/1": _doc("kr-gad-2024/doc_2/1", "Диагностика"),
        "kr-gad-2024/doc_2/2": _doc("kr-gad-2024/doc_2/2", "Диагностика"),
        "kr-gad-2024/doc_3/1": _doc("kr-gad-2024/doc_3/1", "Лечение"),
        "o/4.1/1": _doc("o/4.1/1", "Показания", source="aviandr-ohlp"),
    }

    assert section_headings(docs)["kr-gad-2024"] == ["Диагностика", "Лечение"]


@pytest.mark.anyio
async def test_context_prompt_has_document_headings_and_text() -> None:
    seen: list[str] = []

    async def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.append(str(messages[-1].parts[-1].content))  # type: ignore[union-attr]
        answer = {"context": "  Пункт о показаниях к ЭЭГ при ГТР.  "}
        return ModelResponse(parts=[TextPart(json.dumps(answer, ensure_ascii=False))])

    contextualizer = Contextualizer(
        Llm(FunctionModel(respond, model_name=MODEL_ALIAS), NullRecorder())
    )

    result = await contextualizer.context(
        _doc("k", "Диагностика / ЭЭГ"), ["Диагностика", "Лечение"]
    )

    assert result == "Пункт о показаниях к ЭЭГ при ГТР."
    assert "КР ГТР" in seen[0] and "Диагностика / ЭЭГ" in seen[0]
    assert "Лечение" in seen[0] and "Текст." in seen[0]
