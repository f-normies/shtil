"""Этапы подбора поиска (спека #70, 5.4) и объединённая выдача для разметки (3.2)."""

from retrieval import Config
from stages import (
    EMBEDDINGS,
    RERANKERS,
    passed_factors,
    pool_keys,
    stage_a_configs,
    stage_b_configs,
)


def test_stage_a_changes_one_factor_at_a_time() -> None:
    configs = stage_a_configs()
    names = [c.name for c in configs]

    assert names[0] == "base"
    assert len(names) == len(set(names))
    assert "abbr" in names and "parts" in names and "ctx" in names
    assert "route=bonus" in names and "route=filter" in names
    for model in EMBEDDINGS[1:]:
        assert f"emb={model}" in names
    for model in RERANKERS:
        assert f"rerank={model}@20" in names and f"rerank={model}@40" in names
    assert all("+" not in name for name in names)


def test_factor_passes_with_positive_gain_and_small_possible_loss() -> None:
    diffs = {
        "abbr": {"recall_at_5": (0.03, -0.01, 0.07), "ndcg_at_10": (0.0, -0.02, 0.02)},
        "parts": {"recall_at_5": (0.02, -0.05, 0.08), "ndcg_at_10": (-0.01, -0.03, 0.01)},
        "ctx": {"recall_at_5": (-0.01, -0.03, 0.01), "ndcg_at_10": (0.02, -0.015, 0.05)},
        "emb=bge-m3": {"recall_at_5": (0.01, -0.01, 0.03), "ndcg_at_10": (0.0, -0.01, 0.01)},
        "emb=qwen3-emb-4b": {"recall_at_5": (0.04, 0.0, 0.08), "ndcg_at_10": (0.02, 0.0, 0.04)},
        "rerank=bge-v2-m3@20": {
            "recall_at_5": (0.05, 0.01, 0.09),
            "ndcg_at_10": (0.04, 0.0, 0.08),
        },
        "rerank=bge-v2-m3@40": {
            "recall_at_5": (0.08, 0.03, 0.12),
            "ndcg_at_10": (0.06, 0.02, 0.1),
        },
    }

    passed = passed_factors(diffs)

    assert passed["abbr"] == Config(abbreviations=True)
    assert passed["ctx"] == Config(context=True)
    assert "parts" not in passed
    # Из вариантов одного фактора проходит лучший по recall@5.
    assert passed["embeddings"] == Config(embeddings="qwen3-emb-4b")
    assert passed["reranker"] == Config(reranker="bge-v2-m3", rerank_depth=40)


def test_stage_b_combines_passed_factors() -> None:
    passed = {
        "abbr": Config(abbreviations=True),
        "reranker": Config(reranker="bge-v2-m3", rerank_depth=40),
    }

    names = sorted(c.name for c in stage_b_configs(passed))

    assert names == sorted(["base", "abbr", "rerank=bge-v2-m3@40", "abbr+rerank=bge-v2-m3@40"])


def test_pool_is_union_of_top_10_plus_origin() -> None:
    rankings = [["a", "b", "c"], ["c", "d"], [f"x{i}" for i in range(15)]]

    pool = pool_keys(rankings, origin=["z"], depth=10)

    assert pool[:4] == ["z", "a", "b", "c"]
    assert "x9" in pool and "x10" not in pool
    assert len(pool) == len(set(pool))
