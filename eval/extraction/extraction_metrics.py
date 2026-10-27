"""Совпадения и метрики извлечения из историй болезни (спека #30, раздел 7).

Эталон — `expected.json` историй `knowledge/cases/`. Каждый эталонный факт засчитывается
один раз: диагноз — по коду; препарат — по названию (нормализация ГРЛС и rapidfuzz от 85,
как в `eval/freetext`); анамнез и факторы риска — по цитате: общих слов не меньше половины
более короткой. Среди совпавших препаратов отдельно считаются верные доза, режим и статус.
"""

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from clin_compass.modules.ingestion.analysis import Extraction, FoundFact
from clin_compass_knowledge.grls import normalize
from clin_compass_knowledge.loader import load_knowledge
from clin_compass_knowledge.schemas.case import Case, Expected
from rapidfuzz import fuzz

KNOWLEDGE = Path(__file__).resolve().parents[2] / "knowledge"
FIELDS = ("diagnoses", "medications", "anamnesis", "risk_factors")
# Название препарата у модели и в эталоне: тот же порог, что «похоже» в сверке с ГРЛС.
_SAME_DRUG = 85.0


@dataclass(frozen=True)
class CaseScore:
    case_id: str
    # Поле → (совпало, лишние у модели, пропущено моделью).
    fields: dict[str, tuple[int, int, int]]
    # (верно, всего совпавших препаратов).
    dose: tuple[int, int]
    regimen: tuple[int, int]
    status: tuple[int, int]
    # (верных, всего) среди кодов, которых нет в тексте: их предложила модель.
    codes_proposed: tuple[int, int]
    dropped: int
    kept: int
    # Поле → эталонные факты, которых модель не нашла: для разбора промахов.
    missed: dict[str, list[str]] = field(default_factory=dict)


def load_cases(root: Path = KNOWLEDGE) -> list[Case]:
    """Истории `knowledge/cases/` по порядку id."""
    return sorted(load_knowledge(root).cases.values(), key=lambda c: c.id)


def quotes_overlap(a: str, b: str) -> bool:
    """Общие слова составляют не меньше половины более короткой цитаты (`eval/freetext`)."""
    return _quote_score(a, b) is not None


def dose_equal(a: str | None, b: str | None) -> bool:
    """Дозы равны без учёта пробелов, регистра и десятичной запятой."""
    if a is None or b is None:
        return a is b
    return _dose(a) == _dose(b)


def regimen_equal(a: str | None, b: str | None) -> bool:
    """Режимы совпадают: общих слов не меньше половины более короткого."""
    if a is None or b is None:
        return a is b
    return quotes_overlap(a, b)


def score_case(case_id: str, expected: Expected, found: Extraction) -> CaseScore:
    """Совпадения по полям, доля верных дозы, режима и статуса, кодов модели."""
    fields: dict[str, tuple[int, int, int]] = {}
    missed: dict[str, list[str]] = {}
    codes = [d.icd10 for d in expected.diagnoses]
    _, fields["diagnoses"], missed["diagnoses"] = _match(
        codes, [d.icd10 for d in found.diagnoses], lambda e, p: 1.0 if e == p.upper() else None
    )
    drug_pairs, fields["medications"], missed["medications"] = _match(
        list(expected.medications),
        list(found.medications),
        _drug_score,
        label=lambda e: e.name,
    )
    for name, kind in (("anamnesis", "anamnesis"), ("risk_factors", "risk_factor")):
        facts: list[FoundFact] = [f for f in found.facts if f.field == kind]
        _, fields[name], missed[name] = _match(
            list(getattr(expected, name)),
            facts,
            lambda e, p: _quote_score(e.quote, p.quote),
            label=lambda e: e.quote,
        )
    proposed = [d.icd10 for d in found.diagnoses if not d.code_in_text]
    return CaseScore(
        case_id=case_id,
        fields=fields,
        dose=_share_of(drug_pairs, lambda e, p: dose_equal(e.dose, p.dose)),
        regimen=_share_of(drug_pairs, lambda e, p: regimen_equal(e.regimen, p.regimen)),
        status=_share_of(drug_pairs, lambda e, p: e.status == p.status),
        codes_proposed=(sum(1 for c in proposed if c in codes), len(proposed)),
        dropped=found.dropped,
        kept=len(found.diagnoses) + len(found.medications) + len(found.facts),
        missed=missed,
    )


def aggregate(scores: Sequence[CaseScore]) -> dict[str, Any]:
    """Метрики набора: точность, полнота и F1 по полям, доли верных атрибутов."""
    fields: dict[str, Any] = {}
    for name in (*FIELDS, "all"):
        names = FIELDS if name == "all" else (name,)
        tp, fp, fn = (sum(s.fields[n][k] for s in scores for n in names) for k in range(3))
        fields[name] = _prf(tp, fp, fn)
    kept = sum(s.kept for s in scores)
    dropped = sum(s.dropped for s in scores)
    return {
        "fields": fields,
        "dose_accuracy": _sum_share(s.dose for s in scores),
        "regimen_accuracy": _sum_share(s.regimen for s in scores),
        "status_accuracy": _sum_share(s.status for s in scores),
        "proposed_codes_accuracy": _sum_share(s.codes_proposed for s in scores),
        "proposed_codes": sum(s.codes_proposed[1] for s in scores),
        "dropped_share": _share(dropped, kept + dropped),
        "missed": {
            name: [f"{s.case_id}: {item}" for s in scores for item in s.missed.get(name, [])]
            for name in ("diagnoses", "medications")
        },
    }


def _match[E, P](
    expected: list[E],
    found: list[P],
    score: Callable[[E, P], float | None],
    label: Callable[[E], str] = str,
) -> tuple[list[tuple[E, P]], tuple[int, int, int], list[str]]:
    """Пары «эталон — ответ» с лучшим сходством: каждый факт — не больше одного раза.

    Сначала берутся пары с наибольшим сходством. Так ответ, похожий на два эталонных факта,
    достаётся тому, на который он похож больше.
    """
    candidates = sorted(
        (
            (value, -i, -j)
            for i, item in enumerate(expected)
            for j, answer in enumerate(found)
            if (value := score(item, answer)) is not None
        ),
        reverse=True,
    )
    paired: dict[int, int] = {}
    used: set[int] = set()
    for _, i, j in candidates:
        if -i not in paired and -j not in used:
            paired[-i] = -j
            used.add(-j)
    pairs = [(expected[i], found[paired[i]]) for i in sorted(paired)]
    missed = [label(item) for i, item in enumerate(expected) if i not in paired]
    return pairs, (len(pairs), len(found) - len(pairs), len(missed)), missed


def _drug_score(expected: Any, found: Any) -> float | None:
    ratio = fuzz.ratio(normalize(expected.name), normalize(found.name))
    return ratio if ratio >= _SAME_DRUG else None


def _quote_score(expected: str, found: str) -> float | None:
    """Доля общих слов от более короткой цитаты; меньше половины — не пара."""
    we, wf = _words(expected), _words(found)
    if not we or not wf:
        return None
    share = len(we & wf) / min(len(we), len(wf))
    return share if share >= 0.5 else None


def _share_of[E, P](pairs: list[tuple[E, P]], correct: Callable[[E, P], bool]) -> tuple[int, int]:
    return sum(1 for e, p in pairs if correct(e, p)), len(pairs)


def _words(text: str) -> set[str]:
    return set(re.findall(r"\w+", text.lower().replace("ё", "е")))


def _dose(value: str) -> str:
    return "".join(value.lower().replace(",", ".").split())


def _prf(tp: int, fp: int, fn: int) -> dict[str, Any]:
    precision = _share(tp, tp + fp)
    recall = _share(tp, tp + fn)
    f1 = (
        None
        if precision is None or recall is None or precision + recall == 0
        else round(2 * precision * recall / (precision + recall), 3)
    )
    return {"precision": precision, "recall": recall, "f1": f1, "tp": tp, "fp": fp, "fn": fn}


def _sum_share(pairs: Any) -> float | None:
    correct = total = 0
    for part, whole in pairs:
        correct += part
        total += whole
    return _share(correct, total)


def _share(part: int, whole: int) -> float | None:
    return None if whole == 0 else round(part / whole, 3)
