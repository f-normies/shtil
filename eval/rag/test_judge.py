"""Судьи подбора (спека #70, 3.2 и 6.4). LLM не вызывается: `FunctionModel` по сценарию."""

import json

import pytest
from clin_compass.core.ai.llm import MODEL_ALIAS, Llm
from clin_compass.core.ai.nodes import AiOutputError
from clin_compass.core.ai.runs import NullRecorder
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from judge import ClaimJudge, MustNotJudge, Passage, PointJudge, RelevanceJudge, batches


def _llm(answers: list[dict[str, object]], seen: list[str]) -> Llm:
    queue = list(answers)

    async def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.append(str(messages[-1].parts[-1].content))  # type: ignore[union-attr]
        answer = queue.pop(0) if len(queue) > 1 else queue[0]
        return ModelResponse(parts=[TextPart(json.dumps(answer, ensure_ascii=False))])

    return Llm(FunctionModel(respond, model_name=MODEL_ALIAS), NullRecorder())


def _passage(key: str, size: int = 10) -> Passage:
    return Passage(key=key, title="КР ГТР", heading_path="Лечение", text="т" * size)


def test_batches_respect_count_and_size() -> None:
    passages = [_passage(f"k{i}", 100) for i in range(5)]

    assert [len(b) for b in batches(passages, max_count=2, max_chars=1000)] == [2, 2, 1]
    assert [len(b) for b in batches(passages, max_count=10, max_chars=250)] == [2, 2, 1]


def test_batches_keep_oversized_passage_alone() -> None:
    passages = [_passage("big", 5000), _passage("small", 10)]

    assert [[p.key for p in b] for b in batches(passages, max_count=10, max_chars=1000)] == [
        ["big"],
        ["small"],
    ]


@pytest.mark.anyio
async def test_relevance_grades_are_mapped_back_to_keys() -> None:
    seen: list[str] = []
    judge = RelevanceJudge(_llm([{"F1": 2, "F2": 0}], seen))

    grades = await judge.grade("Когда ЭЭГ?", [_passage("a"), _passage("b")])

    assert grades == {"a": 2, "b": 0}
    assert "[F1]" in seen[0] and "[F2]" in seen[0]


@pytest.mark.anyio
async def test_relevance_retries_after_invalid_answer() -> None:
    judge = RelevanceJudge(_llm([{"F1": 2}, {"F1": 1, "F2": 2}], []))

    assert await judge.grade("q", [_passage("a"), _passage("b")]) == {"a": 1, "b": 2}


@pytest.mark.anyio
async def test_relevance_fails_after_three_invalid_answers() -> None:
    judge = RelevanceJudge(_llm([{"F1": 2}] * 3, []))

    with pytest.raises(AiOutputError):
        await judge.grade("q", [_passage("a"), _passage("b")])


@pytest.mark.anyio
async def test_claim_verdicts_follow_claim_order() -> None:
    judge = ClaimJudge(_llm([{"C1": "supported", "C2": "unsupported"}], []))

    verdicts = await judge.verdicts("q", [("утверждение 1", "цитата 1"), ("утв 2", "цит 2")])

    assert verdicts == ["supported", "unsupported"]


@pytest.mark.anyio
async def test_claim_judge_skips_empty_answer() -> None:
    seen: list[str] = []
    judge = ClaimJudge(_llm([], seen))

    assert await judge.verdicts("q", []) == []
    assert seen == []


CLAIMS = [("Противопоказан при СКФ < 60.", "Почечная недостаточность (СКФ <60 мл/мин).")]


@pytest.mark.anyio
async def test_point_verdicts_follow_point_order_and_show_patient() -> None:
    seen: list[str] = []
    judge = PointJudge(_llm([{"P1": True, "P2": False}], seen))

    verdicts = await judge.covered(
        "авиандр ей можно?", "Ж, 61 год. СКФ 52.", CLAIMS, ["Противопоказан при СКФ < 60.", "Доза"]
    )

    assert verdicts == [True, False]
    assert "Ж, 61 год. СКФ 52." in seen[0]
    assert "[P1] Противопоказан при СКФ < 60." in seen[0]
    assert "Цитата: Почечная недостаточность" in seen[0]


@pytest.mark.anyio
async def test_empty_answer_covers_no_point_without_a_call() -> None:
    seen: list[str] = []
    judge = PointJudge(_llm([{"P1": True}], seen))

    assert await judge.covered("q", None, [], ["пункт"]) == [False]
    assert seen == []


@pytest.mark.anyio
async def test_must_not_verdicts_follow_item_order() -> None:
    seen: list[str] = []
    judge = MustNotJudge(_llm([{"M1": False, "M2": True}], seen))

    verdicts = await judge.present("q", None, CLAIMS, ["Назначить 20 мг", "Разрешить препарат"])

    assert verdicts == [False, True]
    assert "[M2] Разрешить препарат" in seen[0]
    assert "<пациент>" not in seen[0]


@pytest.mark.anyio
async def test_no_items_need_no_call() -> None:
    seen: list[str] = []
    judge = MustNotJudge(_llm([{"M1": True}], seen))

    assert await judge.present("q", None, CLAIMS, []) == []
    assert await judge.present("q", None, [], ["x"]) == [False]
    assert seen == []
