"""Этапы подбора поиска (спека #70, 5.4) и объединённая выдача для разметки (3.2).

Этап A меняет один фактор относительно базы. Фактор проходит, если его разница с базой
по recall@5 или nDCG@10 положительна, а нижняя граница интервала разности выше −0,02.
Из вариантов одного фактора (модели эмбеддингов, ранжировщики, режимы выбора документа)
проходит лучший по recall@5. Этап B перебирает все сочетания прошедших факторов.
"""

from collections.abc import Mapping, Sequence
from dataclasses import fields, replace
from itertools import combinations

from retrieval import Config

EMBEDDINGS = ("giga-3b", "qwen3-emb-4b", "giga-480m", "bge-m3")
# Qwen3-Reranker-4B исключён по времени: ~30 с на вопрос из 40 фрагментов на M3 Max
# (970 токенов в секунду, упор в вычисления), в бою не годится (спека #70, раздел 12).
RERANKERS = ("bge-v2-m3", "lamar-600m")
RERANK_DEPTHS = (20, 40)
MAX_LOSS = -0.02
DECISIVE = ("recall_at_5", "ndcg_at_10")
Diff = tuple[float, float, float]  # средняя разность, нижняя и верхняя граница


def stage_a_configs() -> list[Config]:
    """База и конфигурации с одним изменённым фактором."""
    return [
        Config(),
        *(Config(embeddings=model) for model in EMBEDDINGS[1:]),
        Config(abbreviations=True),
        Config(routing="bonus"),
        Config(routing="filter"),
        Config(parts=True),
        Config(context=True),
        *(
            Config(reranker=model, rerank_depth=depth)
            for model in RERANKERS
            for depth in RERANK_DEPTHS
        ),
    ]


def _factor(name: str) -> str:
    prefix = name.split("=", 1)[0]
    return {"emb": "embeddings", "rerank": "reranker", "route": "routing"}.get(prefix, prefix)


def _passes(diff: Mapping[str, Diff]) -> bool:
    return any(diff[m][0] > 0 and diff[m][1] > MAX_LOSS for m in DECISIVE if m in diff)


def passed_factors(diffs: Mapping[str, Mapping[str, Diff]]) -> dict[str, Config]:
    """Фактор → лучшая прошедшая конфигурация этапа A.

    Args:
        diffs: Имя конфигурации → метрика → разность с базой и её интервал.
    """
    by_name = {c.name: c for c in stage_a_configs()}
    best: dict[str, tuple[float, Config]] = {}
    for name, diff in diffs.items():
        if name == "base" or not _passes(diff):
            continue
        factor = _factor(name)
        gain = diff["recall_at_5"][0]
        if factor not in best or gain > best[factor][0]:
            best[factor] = (gain, by_name[name])
    return {factor: config for factor, (_, config) in best.items()}


def _merge(configs: Sequence[Config]) -> Config:
    base = Config()
    merged = base
    for config in configs:
        changes = {
            f.name: getattr(config, f.name)
            for f in fields(Config)
            if getattr(config, f.name) != getattr(base, f.name)
        }
        merged = replace(merged, **changes)
    return merged


def stage_b_configs(passed: Mapping[str, Config]) -> list[Config]:
    """Все сочетания прошедших факторов, включая базу."""
    factors = sorted(passed)
    return [
        _merge([passed[f] for f in subset])
        for size in range(len(factors) + 1)
        for subset in combinations(factors, size)
    ]


def pool_keys(rankings: Sequence[Sequence[str]], *, origin: Sequence[str], depth: int) -> list[str]:
    """Исходные фрагменты вопроса и первые `depth` каждой выдачи, без повторов."""
    pool = list(dict.fromkeys(origin))
    for ranking in rankings:
        for key in ranking[:depth]:
            if key not in pool:
                pool.append(key)
    return pool
