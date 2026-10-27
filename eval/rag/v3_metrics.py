"""Метрики набора v3 и правило выбора варианта (спека #92, раздел 4).

Значение метрики считается по каждому вопросу; вопрос, к которому метрика неприменима,
даёт None и в среднее не входит. Поиск — по выдаче (`found`) и по фрагментам, показанным
LLM (`shown`). Ответ — по вердиктам судей пунктов и `must_not` (`judge.py`). Доля
подтверждённых утверждений — общая по всем утверждениям, как в v2.

Правило 4.4: лидер — вариант с наибольшим покрытием пунктов. Кандидат равен лидеру по
покрытию (интервал разности содержит 0) и не проигрывает ему значимо по важным пунктам,
опасному ответу, подтверждённым утверждениям и отказу. Победитель — самый быстрый кандидат
в бюджете времени; нет такого — решает человек.
"""

import math
import statistics
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from dataset import grades, relevant_keys
from dataset_v3 import QuestionV3
from rag_metrics import (
    RESAMPLES,
    bootstrap_interval,
    latency,
    ndcg_at,
    paired_interval,
    recall_at,
    reciprocal_rank,
)

SEED = 92
SHOWN = 5
DEPTH = 20
BUDGET_MEDIAN_S = 15.0
BUDGET_P95_S = 25.0
MAIN = "point_coverage"
# Метрики-условия правила 4.4 и направление «лучше».
GUARDS: Mapping[str, bool] = {
    "critical_complete": True,
    "must_not_share": False,
    "supported_share": True,
    "refusal": True,
}
MEAN_METRICS = (
    "recall_at_5",
    "recall_at_20",
    "hit_at_5",
    "mrr",
    "ndcg_at_10",
    "precision_at_5",
    "context_coverage",
    "context_use",
    "point_coverage",
    "critical_complete",
    "must_not_share",
    "refusal",
    "false_refusal",
)
Value = float | None


@dataclass(frozen=True)
class OutcomeV3:
    """Итог варианта на вопросе.

    `found` — выдача поиска по местам, `shown` — фрагменты, которые видела LLM, `cited` —
    фрагмент каждого показанного утверждения, `claim_verdicts` — вердикты судьи утверждений
    в том же порядке. `points` и `must_not` — вердикты судей по пунктам вопроса; None —
    судья не запускался. `stages` — время этапов в секундах.
    """

    question: QuestionV3
    found: tuple[str, ...]
    shown: tuple[str, ...]
    cited: tuple[str, ...]
    claim_verdicts: tuple[str | None, ...]
    points: tuple[bool, ...] | None
    must_not: tuple[bool, ...] | None
    dropped: int
    latency_s: float
    stages: Mapping[str, float] = field(default_factory=dict)


def _share(flags: Sequence[bool]) -> float:
    return sum(flags) / len(flags)


def question_values(o: OutcomeV3) -> dict[str, Value]:
    """Значение каждой метрики на одном вопросе; None — метрика к вопросу неприменима."""
    q = o.question
    relevant = relevant_keys(q)
    graded = set(grades(q))
    shown = set(o.shown)
    evidence = [{e.key for e in p.evidence} for p in q.points]
    critical = [i for i, p in enumerate(q.points) if p.critical]
    empty = not o.cited
    values: dict[str, Value] = dict.fromkeys(MEAN_METRICS)
    if relevant:
        values["recall_at_5"] = recall_at(relevant, o.shown, SHOWN)
        values["recall_at_20"] = recall_at(relevant, o.found, DEPTH)
        values["hit_at_5"] = float(bool(relevant & shown))
        values["mrr"] = reciprocal_rank(relevant, o.found)
        values["ndcg_at_10"] = ndcg_at(grades(q), o.found, 10)
    if o.shown:
        values["precision_at_5"] = len(graded & shown) / len(o.shown)
    if evidence:
        values["context_coverage"] = _share([bool(keys & shown) for keys in evidence])
        values["false_refusal"] = float(empty)
    if o.cited and o.shown:
        values["context_use"] = len(set(o.cited) & shown) / len(o.shown)
    if o.points is not None and q.points:
        values["point_coverage"] = _share(o.points)
        if critical:
            values["critical_complete"] = float(all(o.points[i] for i in critical))
    if o.must_not is not None and q.must_not:
        values["must_not_share"] = float(any(o.must_not))
    if q.kind == "none":
        values["refusal"] = float(empty)
    return values


def _supported(o: OutcomeV3) -> tuple[int, int] | None:
    if any(v is None for v in o.claim_verdicts):
        return None
    return sum(v == "supported" for v in o.claim_verdicts), len(o.claim_verdicts)


def _mean(values: Sequence[float], resamples: int, seed: int) -> dict[str, Any]:
    low, high = bootstrap_interval(
        len(values),
        lambda idx: sum(values[i] for i in idx) / len(idx),
        resamples=resamples,
        seed=seed,
    )
    return {"mean": round(statistics.fmean(values), 4), "ci95": [low, high], "n": len(values)}


def _ratio(pairs: Sequence[tuple[int, int]], resamples: int, seed: int) -> dict[str, Any] | None:
    whole = sum(w for _, w in pairs)
    if not whole:
        return None

    def ratio(idx: list[int]) -> float:
        total = sum(pairs[i][1] for i in idx)
        return sum(pairs[i][0] for i in idx) / total if total else math.nan

    low, high = bootstrap_interval(len(pairs), ratio, resamples=resamples, seed=seed)
    return {"mean": round(sum(p for p, _ in pairs) / whole, 4), "ci95": [low, high]}


def summary_v3(
    outcomes: Sequence[OutcomeV3], *, resamples: int = RESAMPLES, seed: int = SEED
) -> dict[str, Any]:
    """Среднее и 95% интервал каждой метрики, доля подтверждённых утверждений, время."""
    per_question = [question_values(o) for o in outcomes]
    result: dict[str, Any] = {"questions": len(outcomes)}
    for metric in MEAN_METRICS:
        values = [v[metric] for v in per_question if v[metric] is not None]
        result[metric] = _mean(values, resamples, seed) if values else None  # type: ignore[arg-type]
    supported = [_supported(o) for o in outcomes]
    result["supported_share"] = (
        _ratio([s for s in supported if s is not None], resamples, seed)
        if all(s is not None for s in supported)
        else None
    )
    found = [(len(o.cited), len(o.cited) + o.dropped) for o in outcomes]
    result["quotes_found_share"] = _ratio(found, resamples, seed)
    stages = sorted({name for o in outcomes for name in o.stages})
    result["latency"] = {
        "total": latency([o.latency_s for o in outcomes]),
        **{s: latency([o.stages[s] for o in outcomes if s in o.stages]) for s in stages},
    }
    return result


def breakdown(outcomes: Sequence[OutcomeV3], by: str) -> dict[str, dict[str, float | None]]:
    """Средние метрик по группам вопросов: `type` или `style`; без интервалов."""
    groups: dict[str, list[dict[str, Value]]] = {}
    for o in outcomes:
        groups.setdefault(str(getattr(o.question, by)), []).append(question_values(o))
    result = {}
    for group, rows in groups.items():
        means: dict[str, float | None] = {"questions": float(len(rows))}
        for metric in MEAN_METRICS:
            values = [r[metric] for r in rows if r[metric] is not None]
            means[metric] = round(statistics.fmean(values), 4) if values else None  # type: ignore[arg-type]
        result[group] = means
    return result


def _paired(
    a: Sequence[OutcomeV3],
    b: Sequence[OutcomeV3],
    metric: str,
    resamples: int,
    seed: int,
) -> tuple[float, float, float] | None:
    """Разность `a − b` по одним и тем же вопросам и её 95% интервал; None — нечего сравнить."""
    by_id = {o.question.id: o for o in b}
    if sorted(by_id) != sorted(o.question.id for o in a):
        raise ValueError("у прогонов разные вопросы")
    pairs = [(x, by_id[x.question.id]) for x in a]
    if metric == "supported_share":
        rows = [(_supported(x), _supported(y)) for x, y in pairs]
        if any(r is None for row in rows for r in row):
            return None
        return _ratio_diff([(*x, *y) for x, y in rows], resamples, seed)  # type: ignore[misc]
    values = [(question_values(x)[metric], question_values(y)[metric]) for x, y in pairs]
    kept = [(x, y) for x, y in values if x is not None and y is not None]
    if not kept:
        return None
    return paired_interval(
        [x for x, _ in kept], [y for _, y in kept], resamples=resamples, seed=seed
    )


def _ratio_diff(
    rows: Sequence[tuple[int, int, int, int]], resamples: int, seed: int
) -> tuple[float, float, float] | None:
    def diff(idx: Sequence[int]) -> float:
        whole_a = sum(rows[i][1] for i in idx)
        whole_b = sum(rows[i][3] for i in idx)
        if not whole_a or not whole_b:
            return math.nan
        return sum(rows[i][0] for i in idx) / whole_a - sum(rows[i][2] for i in idx) / whole_b

    if math.isnan(diff(range(len(rows)))):
        return None
    low, high = bootstrap_interval(len(rows), diff, resamples=resamples, seed=seed)
    return round(diff(range(len(rows))), 4), low, high


def _loses(diff: tuple[float, float, float] | None, higher_is_better: bool) -> bool:
    """Значимый проигрыш: интервал разности целиком на стороне «хуже»."""
    if diff is None:
        return False
    _, low, high = diff
    return high < 0 if higher_is_better else low > 0


def _in_budget(summary: Mapping[str, Any]) -> bool:
    total = summary["latency"]["total"]
    return bool(total) and total["median_s"] <= BUDGET_MEDIAN_S and total["p95_s"] <= BUDGET_P95_S


def select(
    runs: Mapping[str, Sequence[OutcomeV3]],
    *,
    resamples: int = RESAMPLES,
    seed: int = SEED,
) -> dict[str, Any]:
    """Сравнение вариантов по правилу 4.4: лидер, кандидаты, победитель или None.

    Raises:
        ValueError: У прогона нет вердиктов судей: без них вариант сошёл бы за равный лидеру.
    """
    for name, outcomes in runs.items():
        if any(_unjudged(o) for o in outcomes):
            raise ValueError(f"{name}: судья запускался не по всем ответам")
    summaries = {name: summary_v3(o, resamples=resamples, seed=seed) for name, o in runs.items()}
    leader = max(summaries, key=lambda name: _score(summaries[name]))
    table: dict[str, dict[str, Any]] = {}
    for name, outcomes in runs.items():
        diffs = {
            metric: _paired(outcomes, runs[leader], metric, resamples, seed)
            for metric in (MAIN, *GUARDS)
        }
        main = diffs[MAIN]
        equal = main is None or main[2] >= 0
        safe = not any(_loses(diffs[m], better) for m, better in GUARDS.items())
        table[name] = {
            "summary": summaries[name],
            "vs_leader": diffs,
            "candidate": equal and safe,
            "in_budget": _in_budget(summaries[name]),
        }
    fitting = [n for n, row in table.items() if row["candidate"] and row["in_budget"]]
    winner = min(fitting, key=lambda n: _median(summaries[n])) if fitting else None
    return {"leader": leader, "winner": winner, "runs": table}


def _unjudged(o: OutcomeV3) -> bool:
    return (
        (bool(o.question.points) and o.points is None)
        or (bool(o.question.must_not) and o.must_not is None)
        or any(v is None for v in o.claim_verdicts)
    )


def _score(summary: Mapping[str, Any]) -> float:
    main = summary[MAIN]
    return main["mean"] if main else -math.inf


def _median(summary: Mapping[str, Any]) -> float:
    total = summary["latency"]["total"]
    return total["median_s"] if total else math.inf


def point_losses(outcomes: Sequence[OutcomeV3]) -> dict[str, dict[str, int]]:
    """Разбор потерь пунктов (спека #105, раздел 4) по видам вопроса и в целом.

    Для пункта: где был его фрагмент — показан модели (`shown`), среди 20 кандидатов
    (`top20`), не найден (`missed`) — и сказала ли модель пункт (`said`, `not_said`).
    Ответы без вердиктов судьи пропускаются.
    """
    result: dict[str, Counter[str]] = {}
    for o in outcomes:
        if o.points is None:
            continue
        shown, found = set(o.shown), set(o.found[:DEPTH])
        for point, said in zip(o.question.points, o.points, strict=True):
            keys = {e.key for e in point.evidence}
            where = "shown" if keys & shown else "top20" if keys & found else "missed"
            label = f"{where}_{'said' if said else 'not_said'}"
            for group in ("all", o.question.type):
                result.setdefault(group, Counter())[label] += 1
    return {group: dict(counts) for group, counts in result.items()}
