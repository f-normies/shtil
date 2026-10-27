"""Метрики оценки RAG v2 (спека #70, раздел 4).

Поиск: recall@5, recall@10 и MRR по релевантным фрагментам вопроса (`dataset.relevant_keys`),
nDCG@10 с выигрышем, равным оценке судьи. Ответ: доля найденных цитат, доля подтверждённых
судьёй утверждений, доля вопросов с подтверждённым ответом, пустые ответы на `none`, время.

У каждой метрики — 95% интервал бутстрэпа по вопросам. Две конфигурации сравниваются
попарно: интервал считается для разности на одних и тех же выборках вопросов.

Модуль назван не `metrics.py`: в `eval/freetext` уже есть модуль с таким именем, и pytest
не собрал бы оба набора за один запуск.
"""

import math
import random
import statistics
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from dataset import Question, grades, relevant_keys

RESAMPLES = 2000
SEED = 70
RETRIEVAL_METRICS = ("recall_at_5", "recall_at_10", "mrr", "ndcg_at_10")
Verdict = str  # supported | partial | unsupported


def recall_at(relevant: set[str], found: Sequence[str], k: int) -> float:
    """Доля релевантных фрагментов среди `k` первых найденных."""
    top = set(found[:k])
    return sum(1 for key in relevant if key in top) / len(relevant)


def reciprocal_rank(relevant: set[str], found: Sequence[str]) -> float:
    """1 / место первого релевантного фрагмента; не найден — 0."""
    return next((1 / rank for rank, key in enumerate(found, start=1) if key in relevant), 0.0)


def ndcg_at(grade_of: Mapping[str, int], found: Sequence[str], k: int) -> float:
    """nDCG@k: выигрыш — оценка судьи, скидка — 1/log2(место + 1)."""
    dcg = sum(grade_of.get(key, 0) / math.log2(rank + 1) for rank, key in enumerate(found[:k], 1))
    ideal = sorted(grade_of.values(), reverse=True)[:k]
    idcg = sum(grade / math.log2(rank + 1) for rank, grade in enumerate(ideal, 1))
    return dcg / idcg if idcg else 0.0


def retrieval_scores(question: Question, found: Sequence[str]) -> dict[str, float]:
    """Метрики поиска одного вопроса по ключам выдачи в порядке мест."""
    relevant = relevant_keys(question)
    return {
        "recall_at_5": recall_at(relevant, found, 5),
        "recall_at_10": recall_at(relevant, found, 10),
        "mrr": reciprocal_rank(relevant, found),
        "ndcg_at_10": ndcg_at(grades(question), found, 10),
    }


def bootstrap_interval(
    n: int,
    statistic: Callable[[list[int]], float],
    *,
    resamples: int = RESAMPLES,
    seed: int = SEED,
) -> tuple[float, float]:
    """95% перцентильный интервал статистики по выборкам вопросов с возвращением.

    Args:
        n: Число вопросов.
        statistic: Значение по списку индексов вопросов выборки; `nan` — выборка не годится.
        resamples: Число выборок.
        seed: Зерно; одинаковое зерно даёт одинаковые выборки в попарных сравнениях.
    """
    rng = random.Random(seed)
    values = []
    for _ in range(resamples):
        value = statistic([rng.randrange(n) for _ in range(n)])
        if not math.isnan(value):
            values.append(value)
    values.sort()
    low = values[math.floor((len(values) - 1) * 0.025)]
    high = values[math.ceil((len(values) - 1) * 0.975)]
    return round(low, 4), round(high, 4)


def paired_interval(
    a: Sequence[float], b: Sequence[float], *, resamples: int = RESAMPLES, seed: int = SEED
) -> tuple[float, float, float]:
    """Средняя разность `a − b` по одним и тем же вопросам и её 95% интервал."""
    diffs = [x - y for x, y in zip(a, b, strict=True)]
    low, high = bootstrap_interval(
        len(diffs),
        lambda idx: sum(diffs[i] for i in idx) / len(idx),
        resamples=resamples,
        seed=seed,
    )
    return round(statistics.fmean(diffs), 4), low, high


def _mean_summary(values: Sequence[float], resamples: int, seed: int) -> dict[str, Any]:
    low, high = bootstrap_interval(
        len(values),
        lambda idx: sum(values[i] for i in idx) / len(idx),
        resamples=resamples,
        seed=seed,
    )
    return {"mean": round(statistics.fmean(values), 4), "ci95": [low, high]}


def retrieval_summary(
    per_question: Sequence[Mapping[str, float]], *, resamples: int = RESAMPLES, seed: int = SEED
) -> dict[str, Any]:
    """Среднее и интервал каждой метрики поиска по вопросам."""
    summary: dict[str, Any] = {"questions": len(per_question)}
    for metric in RETRIEVAL_METRICS:
        values = [scores[metric] for scores in per_question]
        summary[metric] = _mean_summary(values, resamples, seed) if values else None
    return summary


@dataclass(frozen=True)
class AnswerOutcome:
    """Итог ответа на вопрос.

    `cited` — фрагменты показанных утверждений, `verdicts` — вердикты судьи по ним в том же
    порядке (None — судья ещё не запускался). `dropped` — утверждения, отброшенные проверкой.
    """

    question: Question
    cited: tuple[str, ...]
    verdicts: tuple[Verdict | None, ...]
    dropped: int
    latency_s: float
    output_tokens: int


def _ratio_summary(
    parts: Sequence[int], wholes: Sequence[int], resamples: int, seed: int
) -> dict[str, Any] | None:
    if sum(wholes) == 0:
        return None

    def ratio(idx: list[int]) -> float:
        whole = sum(wholes[i] for i in idx)
        return sum(parts[i] for i in idx) / whole if whole else math.nan

    low, high = bootstrap_interval(len(parts), ratio, resamples=resamples, seed=seed)
    return {"mean": round(sum(parts) / sum(wholes), 4), "ci95": [low, high]}


def _answered(o: AnswerOutcome) -> int:
    """1, если подтверждённое утверждение ссылается на фрагмент с оценкой 1 или 2."""
    graded = grades(o.question)
    return int(
        any(v == "supported" and k in graded for k, v in zip(o.cited, o.verdicts, strict=True))
    )


def answer_summary(
    outcomes: Sequence[AnswerOutcome], *, resamples: int = RESAMPLES, seed: int = SEED
) -> dict[str, Any]:
    """Метрики ответа по набору; метрики судьи — None, если вердиктов нет."""
    judged = all(v is not None for o in outcomes for v in o.verdicts)
    answerable = [o for o in outcomes if o.question.kind != "none"]
    unanswerable = [o for o in outcomes if o.question.kind == "none"]

    return {
        "questions": len(outcomes),
        "quotes_found_share": _ratio_summary(
            [len(o.cited) for o in outcomes],
            [len(o.cited) + o.dropped for o in outcomes],
            resamples,
            seed,
        ),
        "supported_share": _ratio_summary(
            [sum(v == "supported" for v in o.verdicts) for o in outcomes],
            [len(o.verdicts) for o in outcomes],
            resamples,
            seed,
        )
        if judged
        else None,
        "answered_share": _ratio_summary(
            [_answered(o) for o in answerable], [1] * len(answerable), resamples, seed
        )
        if judged
        else None,
        "empty_on_none_share": _ratio_summary(
            [int(not o.cited) for o in unanswerable], [1] * len(unanswerable), resamples, seed
        ),
        "latency": latency([o.latency_s for o in outcomes]),
        "output_tokens_median": statistics.median([o.output_tokens for o in outcomes])
        if outcomes
        else None,
    }


def _ratio_diff(
    rows: Sequence[tuple[int, int, int, int]], resamples: int, seed: int
) -> tuple[float, float, float]:
    """Разность долей `часть_a / целое_a − часть_b / целое_b` и её 95% интервал."""

    def diff(idx: Sequence[int]) -> float:
        whole_a = sum(rows[i][1] for i in idx)
        whole_b = sum(rows[i][3] for i in idx)
        if not whole_a or not whole_b:
            return math.nan
        return sum(rows[i][0] for i in idx) / whole_a - sum(rows[i][2] for i in idx) / whole_b

    low, high = bootstrap_interval(len(rows), diff, resamples=resamples, seed=seed)
    return round(diff(range(len(rows))), 4), low, high


def answer_diff(
    a: Sequence[AnswerOutcome],
    b: Sequence[AnswerOutcome],
    *,
    resamples: int = RESAMPLES,
    seed: int = SEED,
) -> dict[str, tuple[float, float, float]]:
    """Разность главных метрик ответа `a − b` по одним и тем же вопросам и её 95% интервал.

    Прогоны сводятся по id вопроса.

    Raises:
        ValueError: У прогонов разные вопросы или у утверждения нет вердикта судьи.
    """
    by_id = {o.question.id: o for o in b}
    if sorted(by_id) != sorted(o.question.id for o in a):
        raise ValueError("у прогонов разные вопросы")
    pairs = [(x, by_id[x.question.id]) for x in a]
    if any(v is None for x, y in pairs for v in x.verdicts + y.verdicts):
        raise ValueError("судья запускался не по всем утверждениям")

    def supported(o: AnswerOutcome) -> int:
        return sum(v == "supported" for v in o.verdicts)

    return {
        "supported_share": _ratio_diff(
            [(supported(x), len(x.verdicts), supported(y), len(y.verdicts)) for x, y in pairs],
            resamples,
            seed,
        ),
        "answered_share": _ratio_diff(
            [(_answered(x), 1, _answered(y), 1) for x, y in pairs if x.question.kind != "none"],
            resamples,
            seed,
        ),
    }


def compare_runs(
    runs: Mapping[str, Sequence[AnswerOutcome]], *, resamples: int = RESAMPLES, seed: int = SEED
) -> dict[str, dict[str, Any]]:
    """Сравнение прогонов ответа по правилу спеки #70, 6.5.

    По каждой главной метрике лидер — прогон с наибольшим значением. Прогон равен лучшему,
    если по обеим метрикам интервал его разности с лидером содержит 0.
    """
    summaries = {
        name: answer_summary(o, resamples=resamples, seed=seed) for name, o in runs.items()
    }
    metrics = ("supported_share", "answered_share")
    leaders = {m: max(summaries, key=lambda name: summaries[name][m]["mean"]) for m in metrics}
    table: dict[str, dict[str, Any]] = {}
    for name, outcomes in runs.items():
        vs_best = {
            m: answer_diff(outcomes, runs[leaders[m]], resamples=resamples, seed=seed)[m]
            for m in metrics
        }
        table[name] = {
            "summary": summaries[name],
            "vs_best": vs_best,
            "equal_to_best": all(high >= 0 for _, _, high in vs_best.values()),
        }
    return table


def latency(values: Sequence[float]) -> dict[str, float] | None:
    """Медиана и p95 (ближайший ранг) в секундах; пусто — None."""
    if not values:
        return None
    ordered = sorted(values)
    p95 = ordered[min(len(ordered) - 1, round(0.95 * (len(ordered) - 1)))]
    return {"median_s": round(statistics.median(ordered), 2), "p95_s": round(p95, 2)}
