"""Кэш подбора на диске (спека #70, 5.1)."""

from pathlib import Path

import numpy as np

from cache import JsonStore, VectorStore, text_key


def test_text_key_depends_on_role_and_text() -> None:
    assert text_key("doc", "текст") != text_key("query", "текст")
    assert text_key("doc", "текст") == text_key("doc", "текст")
    assert len(text_key("doc", "текст")) == 24


def test_vector_store_persists_and_reports_missing(tmp_path: Path) -> None:
    store = VectorStore(tmp_path / "giga.npz")
    store.put({"a": np.array([1.0, 2.0]), "b": np.array([3.0, 4.0])})

    reopened = VectorStore(tmp_path / "giga.npz")

    assert reopened.missing(["a", "c"]) == ["c"]
    np.testing.assert_allclose(reopened.get("b"), [3.0, 4.0])


def test_vector_store_keeps_float32(tmp_path: Path) -> None:
    store = VectorStore(tmp_path / "m.npz")
    store.put({"a": np.array([0.1, 0.2], dtype=np.float64)})

    assert VectorStore(tmp_path / "m.npz").get("a").dtype == np.float32


def test_json_store_persists_values(tmp_path: Path) -> None:
    store = JsonStore(tmp_path / "rerank" / "bge.json")
    store.put({"q|d": 0.5})

    reopened = JsonStore(tmp_path / "rerank" / "bge.json")

    assert reopened.get("q|d") == 0.5
    assert reopened.missing(["q|d", "q|x"]) == ["q|x"]
