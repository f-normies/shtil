"""Согласие двух судей релевантности (спека #70, 3.2)."""

import pytest

from labeling import agreement, label_cache_key


def test_agreement_counts_exact_and_relevance_matches() -> None:
    pairs = [(2, 2), (0, 0), (1, 2), (2, 0), (1, 1)]

    result = agreement(pairs)

    # exact — та же оценка; relevant — согласие в «оценка ≥ 1»; answers — в «оценка 2».
    assert result == {"pairs": 5, "exact": 0.6, "relevant": 0.8, "answers": 0.6}


def test_agreement_without_pairs_is_empty() -> None:
    assert agreement([]) == {"pairs": 0, "exact": None, "relevant": None, "answers": None}


def test_cache_key_keeps_old_format_for_base_model() -> None:
    base = label_cache_key("qwen3.8-27b", "v1", "вопрос", "текст")
    other = label_cache_key("qwen3.6-35b-a3b", "v1", "вопрос", "текст")

    assert base != other
    # Ключ базовой модели — как до добавления модели: прежние оценки остаются в кэше.
    from cache import text_key

    assert base == text_key("label", "v1\nвопрос\nтекст")


@pytest.mark.parametrize("model", ["qwen3.6-35b-a3b", "gemma-4-26b-a4b"])
def test_cache_key_depends_on_model(model: str) -> None:
    assert label_cache_key(model, "v1", "q", "t") != label_cache_key("qwen3.8-27b", "v1", "q", "t")
