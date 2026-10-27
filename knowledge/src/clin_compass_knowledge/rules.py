"""Проверка условия правила по ответам и баллам шкал."""

import operator
from collections.abc import Callable, Mapping

from clin_compass_knowledge.schemas.rule import Condition, Rule

_OPS: dict[str, Callable[[float, float], bool]] = {
    ">=": operator.ge,
    ">": operator.gt,
    "<=": operator.le,
    "<": operator.lt,
    "==": operator.eq,
}


def evaluate(rule: Rule | Condition, results: Mapping[str, Mapping[str, float]]) -> bool:
    """Срабатывает ли правило. `results` — `{scale_id: {item_id или score_id: число}}`.

    Сравнение, для которого нет данных, ложно: незаполненная шкала сигнала не даёт.
    """
    cond = rule.when if isinstance(rule, Rule) else rule
    if cond.any is not None:
        return any(evaluate(c, results) for c in cond.any)
    if cond.all is not None:
        return all(evaluate(c, results) for c in cond.all)
    assert cond.scale is not None and cond.op is not None and cond.value is not None
    key = cond.item or cond.score
    actual = results.get(cond.scale, {}).get(key or "")
    if actual is None:
        return False
    return _OPS[cond.op](actual, cond.value)
