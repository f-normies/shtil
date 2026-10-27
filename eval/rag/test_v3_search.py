"""Варианты подбора v3 и выдача по ним (спека #92, раздел 5) на подставных источниках."""

import json

import pytest
from clin_compass.core.ai.llm import MODEL_ALIAS, Llm
from clin_compass.core.ai.runs import NullRecorder
from clin_compass.modules.knowledge.search import fuse_queries
from clin_compass.modules.knowledge.service import KnowledgeAi
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from dataset_v3 import load_questions_v3
from retrieval import Config
from test_retrieval import FakeSources, _engine
from v3_search import Variant, _ranked, plan, rank


@pytest.mark.parametrize(
    "name",
    [
        "base",
        "rw=one",
        "rw=multi+scales",
        "rw=patient+min=-2.0",
        "premise+verify",
        "think=context",
        "rw=one+scales+min=0.0+premise+verify+think=always",
        "rw=one+rq=question",
        "d2q+rw=one+rq=question+aspects+claims=8",
        "d2q",
    ],
)
def test_variant_name_round_trips(name: str) -> None:
    assert Variant.from_name(name).name == name


def test_variant_parts() -> None:
    variant = Variant.from_name("rw=multi+scales+min=-1.5+verify")

    assert (variant.rewrite, variant.scales, variant.min_score, variant.verify) == (
        "multi",
        True,
        -1.5,
        True,
    )
    assert variant.retrieval_name == "rw=multi+scales+min=-1.5"
    assert Variant.from_name("premise").retrieval_name == "base"


def test_unknown_part_is_rejected() -> None:
    with pytest.raises(ValueError, match="hyde"):
        Variant.from_name("hyde")


def test_plan_mirrors_production_queries() -> None:
    rewrites = ["запрос 1", "запрос 2"]

    assert plan(Variant(), "вопрос", "Ж, 61", rewrites) == (["вопрос"], "вопрос")
    assert plan(Variant(rewrite="multi"), "вопрос", None, rewrites) == (
        ["вопрос", "запрос 1", "запрос 2"],
        "запрос 1",
    )
    assert plan(Variant(rewrite="one"), "вопрос", None, []) == (["вопрос"], "вопрос")
    assert plan(Variant(rewrite="patient"), "вопрос", "Ж, 61", []) == (
        ["вопрос\nЖ, 61"],
        "вопрос\nЖ, 61",
    )
    assert plan(Variant(rewrite="patient"), "вопрос", None, []) == (["вопрос"], "вопрос")


def _sources() -> FakeSources:
    return FakeSources(
        vector={"q": ["g/1", "g/2"], "r": ["o/1", "g/2"]},
        text={"q": ["g/2"], "r": ["d/1"]},
        scores={"g/1": 1.0, "g/2": -1.0, "o/1": 3.0, "d/1": -5.0},
    )


def test_single_query_is_reranked_by_its_query() -> None:
    sources = _sources()

    keys, scores = rank(_engine(sources), ["q"], "q", min_score=None)

    assert keys == ["g/1", "g/2"]
    assert scores == {"g/1": 1.0, "g/2": -1.0}
    assert ("rerank", "q") in sources.queries


def test_queries_are_fused_like_production_and_reranked_by_rerank_query() -> None:
    sources = _sources()
    engine = _engine(sources)

    keys, _ = rank(engine, ["q", "r"], "r", min_score=None)

    lists = [engine.rank(Config(), "q"), engine.rank(Config(), "r")]
    fused = fuse_queries(lists)  # type: ignore[arg-type]
    assert set(keys) == set(fused)
    assert keys == ["o/1", "g/1", "g/2", "d/1"]
    reranked = [query for kind, query in sources.queries if kind == "rerank"]
    assert reranked == ["r"]


def test_threshold_keeps_only_scored_fragments_above_it() -> None:
    keys, _ = rank(_engine(_sources()), ["q", "r"], "r", min_score=0.0)

    assert keys == ["o/1", "g/1"]


def test_plan_drops_rewrite_equal_to_question() -> None:
    assert plan(Variant(rewrite="one"), "Вопрос", None, ["вопрос "]) == (["Вопрос"], "Вопрос")


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["off", "one", "multi", "patient"])
@pytest.mark.parametrize("patient", [None, "Ж, 61 год."])
async def test_plan_matches_production_queries(mode: str, patient: str | None) -> None:
    rewrites = ["запрос 1", "запрос 2"][: 1 if mode == "one" else 2]

    async def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart(json.dumps({"queries": rewrites}))])

    llm = Llm(FunctionModel(respond, model_name=MODEL_ALIAS), NullRecorder())
    ai = KnowledgeAi(None, llm, rewrite=mode)  # type: ignore[arg-type]

    produced = await ai.queries("вопрос", context=patient)

    assert produced == plan(
        Variant.from_name(f"rw={mode}" if mode != "off" else "base"), "вопрос", patient, rewrites
    )


@pytest.mark.anyio
async def test_rerank_by_question_matches_production_queries() -> None:
    async def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart(json.dumps({"queries": ["запрос 1"]}))])

    llm = Llm(FunctionModel(respond, model_name=MODEL_ALIAS), NullRecorder())
    ai = KnowledgeAi(None, llm, rewrite="one", rerank_query="question")  # type: ignore[arg-type]

    produced = await ai.queries("вопрос", context="Ж, 61 год.")

    variant = Variant.from_name("rw=one+rq=question")
    assert produced == plan(variant, "вопрос", "Ж, 61 год.", ["запрос 1"])
    assert produced[1] == "вопрос"


def test_committed_set_loads() -> None:
    assert len(load_questions_v3()) == 300


def test_rerank_by_question_keeps_rewrite_for_search() -> None:
    variant = Variant.from_name("rw=one+rq=question")

    assert plan(variant, "вопрос", "Ж, 61", ["запрос"]) == (["вопрос", "запрос"], "вопрос")


def test_new_feature_parts() -> None:
    variant = Variant.from_name("d2q+aspects+claims=8")

    assert (variant.d2q, variant.aspects, variant.claims) == (True, True, 8)
    assert variant.retrieval_name == "d2q"


class FakeQuestions:
    """Выдачи поиска по вопросам к фрагментам заданы заранее."""

    def __init__(self, vector: dict[str, list[str]], text: dict[str, list[str]]) -> None:
        self._vector, self._text = vector, text

    def vector(self, query: str) -> list[str]:
        return self._vector[query]

    def full_text(self, query: str) -> list[str]:
        return self._text[query]


def test_fragment_questions_add_two_lists_to_hybrid_search() -> None:
    # Боевого поиска по вопросам к фрагментам нет (ADR-0011): сверка — со спекой #105, 5.1.
    sources = _sources()
    engine = _engine(sources)
    questions = FakeQuestions({"q": ["d/1", "g/2"]}, {"q": ["d/1"]})

    keys, _ = rank(engine, ["q"], "q", min_score=None, questions=questions)

    expected = fuse_queries(
        [sources.vector(Config(), "q"), sources.full_text(Config(), "q"), ["d/1", "g/2"], ["d/1"]]  # type: ignore[list-item]
    )
    assert set(keys) == {str(k) for k in expected}
    assert "d/1" in keys


def test_fragment_questions_fuse_four_lists_in_spec_order() -> None:
    # Спека #105, 5.1: поиск по фрагментам, затем по вопросам к ним; слияние — RRF
    # `fuse_queries`. Тест сверяет подбор со спекой. Боевого поиска по четырём спискам нет.
    sources = _sources()
    questions = FakeQuestions({"q": ["d/1", "g/2"]}, {"q": ["d/1"]})

    fused = _ranked(_engine(sources), "q", questions)

    lists = [
        sources.vector(Config(), "q"),
        sources.full_text(Config(), "q"),
        ["d/1", "g/2"],
        ["d/1"],
    ]
    assert fused == [str(k) for k in fuse_queries(lists)]  # type: ignore[arg-type]
