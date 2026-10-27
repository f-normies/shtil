"""Сборка выдачи по конфигурации подбора (спека #70, 5.3) на подставных источниках."""

from collections.abc import Sequence

import numpy as np

from retrieval import Config, Doc, Engine, cosine_ranking
from variants import Abbreviations


def _doc(key: str, source: str = "kr-gad-2024", text: str = "текст") -> Doc:
    return Doc(key=key, source_id=source, title="КР", heading_path="Раздел", text=text)


class FakeSources:
    """Ответы поисков заданы заранее; запоминает, какие запросы пришли."""

    def __init__(
        self,
        vector: dict[str, list[str]],
        text: dict[str, list[str]],
        scores: dict[str, float] | None = None,
    ) -> None:
        self._vector = vector
        self._text = text
        self._scores = scores or {}
        self.queries: list[tuple[str, str]] = []

    def vector(self, config: Config, query: str) -> list[str]:
        self.queries.append(("vector", query))
        return self._vector[query]

    def full_text(self, config: Config, query: str) -> list[str]:
        self.queries.append(("text", query))
        return self._text[query]

    def rerank(self, model: str, query: str, keys: Sequence[str]) -> dict[str, float]:
        self.queries.append(("rerank", query))
        return {key: self._scores.get(key, 0.0) for key in keys}


DOCS = {
    "g/1": _doc("g/1"),
    "g/2": _doc("g/2"),
    "o/1": _doc("o/1", "aviandr-ohlp"),
    "d/1": _doc("d/1", "kr-depression-2024"),
}
TERMS = {"aviandr-ohlp": ["авиандр"], "kr-gad-2024": ["гтр"]}


def _engine(sources: FakeSources, abbreviations: Abbreviations | None = None) -> Engine:
    return Engine(
        docs=DOCS,
        sources=sources,
        abbreviations=abbreviations or Abbreviations.build([], {}),
        terms=TERMS,
    )


def test_base_config_fuses_like_production() -> None:
    sources = FakeSources({"q": ["g/1", "g/2"]}, {"q": ["g/2", "d/1"]})

    assert _engine(sources).rank(Config(), "q") == ["g/2", "g/1", "d/1"]


def test_abbreviations_expand_both_queries() -> None:
    abbreviations = Abbreviations.build([{"ГТР": "генерализованное тревожное расстройство"}], {})
    expanded_vector = "ГТР? (генерализованное тревожное расстройство)"
    expanded_text = "ГТР? генерализованное тревожное расстройство"
    sources = FakeSources({expanded_vector: ["g/1"]}, {expanded_text: ["g/2"]})

    _engine(sources, abbreviations).rank(Config(abbreviations=True), "ГТР?")

    assert ("vector", expanded_vector) in sources.queries
    assert ("text", expanded_text) in sources.queries


def test_routing_bonus_lifts_chosen_source() -> None:
    query = "Авиандр при беременности?"
    sources = FakeSources({query: ["g/1", "g/2", "o/1"]}, {query: ["g/1", "o/1"]})

    ranked = _engine(sources).rank(Config(routing="bonus"), query)

    assert ranked[0] == "o/1"


def test_routing_filter_keeps_only_chosen_source() -> None:
    query = "Авиандр при беременности?"
    sources = FakeSources({query: ["g/1", "o/1"]}, {query: ["d/1"]})

    assert _engine(sources).rank(Config(routing="filter"), query) == ["o/1"]


def test_routing_without_chosen_source_changes_nothing() -> None:
    sources = FakeSources({"q": ["g/1", "o/1"]}, {"q": []})

    assert _engine(sources).rank(Config(routing="filter"), "q") == ["g/1", "o/1"]


def test_reranker_reorders_top_depth_and_keeps_tail() -> None:
    sources = FakeSources({"q": ["g/1", "g/2", "o/1"]}, {"q": []}, scores={"g/1": 0.1, "g/2": 0.9})

    ranked = _engine(sources).rank(Config(reranker="bge", rerank_depth=2), "q")

    assert ranked == ["g/2", "g/1", "o/1"]


def test_config_name_lists_only_changed_factors() -> None:
    assert Config().name == "base"
    assert Config(embeddings="bge-m3", reranker="bge", rerank_depth=20).name == (
        "emb=bge-m3+rerank=bge@20"
    )


def test_cosine_ranking_by_best_part() -> None:
    query = np.array([1.0, 0.0])
    parts = {
        "a": np.array([[0.0, 1.0], [0.9, 0.1]]),
        "b": np.array([[0.7, 0.7]]),
    }

    assert cosine_ranking(query, parts, limit=40) == ["a", "b"]


def test_config_round_trips_through_name() -> None:
    configs = [
        Config(),
        Config(embeddings="qwen3-emb-4b", abbreviations=True, routing="bonus"),
        Config(parts=True, context=True, reranker="qwen3-rr-4b", rerank_depth=20),
    ]

    assert [Config.from_name(c.name) for c in configs] == configs
