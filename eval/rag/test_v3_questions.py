"""Вопросы к фрагментам в подборе (спека #105, 5.1): кэш генерации и поиск по ним."""

from pathlib import Path

import numpy as np

from cache import JsonStore, VectorStore, text_key
from retrieval import Doc
from v3_questions import QuestionIndex, cached_questions, questions_key


def _doc(key: str, text: str) -> Doc:
    return Doc(key=key, source_id="aviandr-ohlp", title="ОХЛП", heading_path="4.3", text=text)


def test_cached_questions_are_found_by_fragment_text(tmp_path: Path) -> None:
    store = JsonStore(tmp_path / "q.json")
    docs = {"a/1": _doc("a/1", "текст 1"), "a/2": _doc("a/2", "текст 2")}
    store.put({questions_key("v1", docs["a/1"]): ["вопрос 1", "вопрос 2"]})

    assert cached_questions(store, "v1", docs) == {"a/1": ["вопрос 1", "вопрос 2"]}
    assert cached_questions(store, "v2", docs) == {}


def test_vector_search_ranks_fragments_by_their_best_question(tmp_path: Path) -> None:
    vectors = VectorStore(tmp_path / "v.npz")
    vectors.put(
        {
            text_key("doc", "далёкий"): np.array([0.0, 1.0]),
            text_key("doc", "близкий"): np.array([1.0, 0.1]),
            text_key("doc", "средний"): np.array([1.0, 1.0]),
            text_key("query", "запрос"): np.array([1.0, 0.0]),
        }
    )
    index = QuestionIndex.build(
        {"a/1": ["далёкий", "близкий"], "a/2": ["средний"]}, vectors, {"запрос": ["a/2"]}
    )

    assert index.vector("запрос") == ["a/1", "a/2"]
    assert index.full_text("запрос") == ["a/2"]
