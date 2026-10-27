"""Подсчёт балла шкалы: сумма `value` выбранных ответов и порог тяжести."""

from collections.abc import Mapping
from dataclasses import dataclass

from clin_compass_knowledge.schemas.scale import Band, Scale, Score


class ScoringError(ValueError):
    """Ответы не подходят к шкале."""


@dataclass(frozen=True)
class ScoreResult:
    """Балл одной суммы и порог, в который он попал."""

    value: int
    band: Band


def score_range(scale: Scale, score_def: Score) -> tuple[int, int]:
    """Минимальная и максимальная возможная сумма."""
    items = [scale.item(i) for i in score_def.items]
    lows = [min(o.value for o in it.options) for it in items if it]
    highs = [max(o.value for o in it.options) for it in items if it]
    return sum(lows), sum(highs)


def score(scale: Scale, answers: Mapping[str, int]) -> dict[str, ScoreResult]:
    """Баллы всех сумм шкалы. `answers` — `{item_id: value}`, ответы на все пункты."""
    for item_id in answers:
        if scale.item(item_id) is None:
            raise ScoringError(f"нет пункта {item_id} в шкале {scale.id}")
    for it in scale.items:
        if it.id not in answers:
            raise ScoringError(f"нет ответа на пункт {it.id}")
        if answers[it.id] not in {o.value for o in it.options}:
            raise ScoringError(f"ответ {answers[it.id]} на пункт {it.id} не из вариантов")

    results = {}
    for score_def in scale.scores:
        total = sum(answers[i] for i in score_def.items)
        band = next((b for b in score_def.bands if b.min <= total <= b.max), None)
        if band is None:  # шкала не прошла проверку порогов
            raise ScoringError(f"сумма {score_def.id} = {total} не попадает ни в один порог")
        results[score_def.id] = ScoreResult(total, band)
    return results
