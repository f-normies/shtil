"""Записи прогонов v3, правило этапа A, слияние оценок разметки (спеки #92 и #105).

Запись прогона ответа — словарь JSON: выдача (`found`), показанные LLM фрагменты (`shown`),
утверждения с цитатами, время этапов и вердикты судей (None — судьи ещё не запускались).
В записях есть цитаты КР, поэтому файлы прогонов не коммитятся (`.gitignore`).
"""

from collections.abc import Mapping
from typing import Any

from dataset import Question, Relevant
from dataset_v3 import QuestionV3, evidence_keys
from v3_metrics import OutcomeV3

# Правило этапа A — как в #70: разница положительна, нижняя граница интервала выше −0,02.
MAX_LOSS = -0.02
STAGE_A_METRICS = ("recall_at_5", "context_coverage")
Diff = tuple[float, float, float]


def outcome_from_record(record: Mapping[str, Any], question: QuestionV3) -> OutcomeV3:
    """Итог для метрик из записи прогона.

    Raises:
        ValueError: Вердиктов судьи по пунктам или `must_not` не столько, сколько их в эталоне:
            эталон поправили после судей, вердикты сдвинулись бы на чужие пункты.
    """
    claims = record["claims"]
    verdicts = record.get("verdicts")
    points = record.get("points")
    must_not = record.get("must_not")
    for name, judged, expected in (
        ("пунктам", points, question.points),
        ("must_not", must_not, question.must_not),
    ):
        if judged is not None and len(judged) != len(expected):
            raise ValueError(
                f"{question.id}: вердиктов по {name} {len(judged)}, в эталоне {len(expected)}: "
                "эталон изменился после судей, перезапустите судей на этих ответах"
            )
    return OutcomeV3(
        question=question,
        found=tuple(record["found"]),
        shown=tuple(record["shown"]),
        cited=tuple(c["key"] for c in claims),
        claim_verdicts=tuple(verdicts) if verdicts is not None else (None,) * len(claims),
        points=tuple(points) if points is not None else None,
        must_not=tuple(must_not) if must_not is not None else None,
        dropped=record["dropped"],
        latency_s=record["latency_s"],
        stages=dict(record.get("stages") or {}),
    )


def passes_stage_a(
    v3_diffs: Mapping[str, Diff], v2_diff: Diff, *, require_gain: bool = True
) -> bool:
    """Фактор поиска проходит этап A (спека #92, 7).

    На dev v3 разница с базой положительна по recall@5 или по покрытию пунктов контекстом,
    и нижняя граница её интервала выше −0,02; на dev v2 нижняя граница разности recall@5 выше
    −0,02. Шкалам (`require_gain=False`) достаточно не проиграть ни по одной метрике.
    """
    if v2_diff[1] <= MAX_LOSS:
        return False
    if not require_gain:
        return all(v3_diffs[m][1] > MAX_LOSS for m in STAGE_A_METRICS)
    return any(v3_diffs[m][0] > 0 and v3_diffs[m][1] > MAX_LOSS for m in STAGE_A_METRICS)


def merge_grades(
    question: QuestionV3, judged: Mapping[str, int], fingerprints: Mapping[str, str]
) -> QuestionV3:
    """Вопрос с оценками судьи по объединённой выдаче (спека #92, 3.4).

    Доказательства пунктов всегда с оценкой 2. Остальные фрагменты — по оценке судьи, 0 не
    хранится. У вопроса без ответа в базе (`kind: none`) судья не может поставить 2:
    разметчик с документами решил, что ответа нет; такая оценка становится 1.
    """
    evidence = evidence_keys(question)
    grades = {r.key: r.grade for r in question.relevant}
    prints = {r.key: r.fingerprint for r in question.relevant}
    for key, grade in judged.items():
        if key in evidence:
            continue
        grades[key] = min(grade, 1) if question.kind == "none" else grade
        prints[key] = fingerprints[key]
    for key in evidence:
        grades[key] = 2
    relevant = [
        Relevant(key=key, fingerprint=prints[key], grade=grade)  # type: ignore[arg-type]
        for key, grade in sorted(grades.items())
        if grade > 0
    ]
    return question.model_copy(update={"relevant": relevant})


def merge_v2_grades(
    question: Question, judged: Mapping[str, int], fingerprints: Mapping[str, str]
) -> Question:
    """Вопрос v2 с оценками судьи для фрагментов, которых в наборе ещё нет (спека #105, 3.4).

    Оценки #70 не меняются и остаются на своих местах, новые дописываются в конец по ключу.
    В v2 хранятся только оценки выше 0, поэтому судья видит и
    фрагменты, которые #70 оценил нулём; новая оценка для них только добавляется. У вопроса
    без ответа (`kind: none`) оценка 2 становится 1, как в `merge_grades`.
    """
    known = {r.key for r in question.relevant}
    added = [
        Relevant(
            key=key,
            fingerprint=fingerprints[key],
            grade=min(grade, 1) if question.kind == "none" else grade,  # type: ignore[arg-type]
        )
        for key, grade in judged.items()
        if key not in known and grade > 0
    ]
    relevant = [*question.relevant, *sorted(added, key=lambda r: r.key)]
    return question.model_copy(update={"relevant": relevant})


def none_with_answer(kind: str, judged: Mapping[str, int]) -> list[str]:
    """Фрагменты, которые судья оценил 2 у вопроса без ответа в базе (`kind: none`).

    В наборе их оценка понижена до 1 (`merge_grades`). Ключи пишутся в итог разметки:
    по ним медик решает, верен ли вид вопроса. У других видов список пуст.
    """
    if kind != "none":
        return []
    return sorted(key for key, grade in judged.items() if grade == 2)


def unjudged(record: Mapping[str, Any]) -> dict[str, Any]:
    """Запись прогона без вердиктов судей: `judge --force` оценит её заново после правки эталона."""
    return {**record, "verdicts": None, "points": None, "must_not": None}
