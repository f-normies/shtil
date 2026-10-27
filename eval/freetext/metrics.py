"""Эталонный набор и метрики разбора дневника и чата (спека #32, раздел 7.2).

Находка модели совпадает с эталонной, если у них одна категория и цитаты перекрываются
хотя бы наполовину (по словам). Формулировка находки не сравнивается: она свободная.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from clin_compass.modules.freetext.analysis import FoundSign, TextFinding
from clin_compass.modules.freetext.schemas import AdherenceEvent, FindingCategory, SourceType
from clin_compass_knowledge.grls import Check, normalize
from clin_compass_knowledge.loader import load_yaml
from pydantic import BaseModel, ConfigDict
from rapidfuzz import fuzz

CASES = Path(__file__).resolve().parent / "cases.yaml"
CATEGORIES: tuple[FindingCategory, ...] = ("symptom", "side_effect", "adherence", "medication")
# Название препарата у модели и в эталоне: то же, что и порог «похоже» в сверке с ГРЛС.
_SAME_DRUG = 85.0


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ExpectedFinding(_Model):
    category: FindingCategory
    quote: str
    adherence_event: AdherenceEvent | None = None


class ExpectedDrug(_Model):
    """Название, как его назвал пациент (в начальной форме), и верный итог сверки."""

    name: str
    check: Check


class Case(_Model):
    id: str
    source_type: SourceType
    transcript: bool = False
    text: str
    findings: list[ExpectedFinding] = []
    drugs: list[ExpectedDrug] = []
    signs: list[str] = []


@dataclass(frozen=True)
class Prediction:
    """Ответ разбора по одному тексту: находки и признаки обоих этапов."""

    findings: tuple[TextFinding, ...]
    risk_signs: tuple[FoundSign, ...]
    extract_signs: tuple[FoundSign, ...]
    dropped: int


@dataclass(frozen=True)
class CaseScore:
    case_id: str
    # Категория → (совпало, лишние у модели, пропущено моделью).
    findings: dict[str, tuple[int, int, int]]
    adherence_correct: int
    adherence_matched: int
    drugs_correct: int
    drugs_expected: int
    # Ожидаемые препараты, которые модель не назвала или сверка дала другой итог.
    drugs_missed: tuple[str, ...]
    risk_expected: bool
    risk_check: bool
    risk_extract: bool
    # (виды в эталоне, виды у модели).
    risk_check_kinds: tuple[set[str], set[str]]
    risk_extract_kinds: tuple[set[str], set[str]]
    dropped: int
    kept: int


def load_cases(path: Path = CASES) -> list[Case]:
    """Эталонный набор; повтор `id` — ошибка."""
    data = load_yaml(path.read_text(encoding="utf-8"))
    cases = [Case.model_validate(raw) for raw in data["cases"]]
    ids = [c.id for c in cases]
    repeated = sorted({i for i in ids if ids.count(i) > 1})
    if repeated:
        raise ValueError(f"повторяются id: {repeated}")
    return cases


def quotes_overlap(a: str, b: str) -> bool:
    """Общие слова составляют не меньше половины более короткой цитаты."""
    wa, wb = _words(a), _words(b)
    if not wa or not wb:
        return False
    return len(wa & wb) / min(len(wa), len(wb)) >= 0.5


def _words(text: str) -> set[str]:
    return set(re.findall(r"\w+", text.lower().replace("ё", "е")))


def score_case(case: Case, prediction: Prediction) -> CaseScore:
    """Совпадения находок, событий приёма, сверки препаратов и признаков риска по тексту."""
    used: set[int] = set()
    pairs: list[tuple[ExpectedFinding, TextFinding]] = []
    counts = {c: [0, 0, 0] for c in CATEGORIES}
    for expected in case.findings:
        index = next(
            (
                i
                for i, p in enumerate(prediction.findings)
                if i not in used
                and p.category == expected.category
                and quotes_overlap(p.quote, expected.quote)
            ),
            None,
        )
        if index is None:
            counts[expected.category][2] += 1
            continue
        used.add(index)
        counts[expected.category][0] += 1
        pairs.append((expected, prediction.findings[index]))
    for i, p in enumerate(prediction.findings):
        if i not in used:
            counts[p.category][1] += 1
    adherence = [(e, p) for e, p in pairs if e.category == "adherence"]
    missed = tuple(d.name for d in case.drugs if not _drug_checked(d, prediction.findings))
    expected_kinds = set(case.signs)
    return CaseScore(
        case_id=case.id,
        findings={c: (n[0], n[1], n[2]) for c, n in counts.items()},
        adherence_correct=sum(1 for e, p in adherence if e.adherence_event == p.adherence_event),
        adherence_matched=len(adherence),
        drugs_correct=len(case.drugs) - len(missed),
        drugs_expected=len(case.drugs),
        drugs_missed=missed,
        risk_expected=bool(case.signs),
        risk_check=bool(prediction.risk_signs),
        risk_extract=bool(prediction.extract_signs),
        risk_check_kinds=(expected_kinds, {s.kind for s in prediction.risk_signs}),
        risk_extract_kinds=(expected_kinds, {s.kind for s in prediction.extract_signs}),
        dropped=prediction.dropped,
        kept=len(prediction.findings),
    )


def _drug_checked(expected: ExpectedDrug, findings: Sequence[TextFinding]) -> bool:
    """Модель назвала этот препарат, и сверка дала ожидаемый итог."""
    name = normalize(expected.name)
    for f in findings:
        if f.drug is None:
            continue
        if (
            fuzz.ratio(normalize(f.drug.name), name) >= _SAME_DRUG
            and f.drug.check == expected.check
        ):
            return True
    return False


def aggregate(scores: Sequence[CaseScore]) -> dict[str, Any]:
    """Метрики набора для JSON результата и таблицы в README."""
    findings: dict[str, Any] = {}
    for category in (*CATEGORIES, "all"):
        cats = CATEGORIES if category == "all" else (category,)
        tp, fp, fn = (sum(s.findings[c][k] for s in scores for c in cats) for k in range(3))
        findings[category] = _prf(tp, fp, fn)
    kept = sum(s.kept for s in scores)
    dropped = sum(s.dropped for s in scores)
    return {
        "findings": findings,
        "adherence_accuracy": _share(
            sum(s.adherence_correct for s in scores), sum(s.adherence_matched for s in scores)
        ),
        "drugs_accuracy": _share(
            sum(s.drugs_correct for s in scores), sum(s.drugs_expected for s in scores)
        ),
        "drugs_missed": [f"{s.case_id}: {name}" for s in scores for name in s.drugs_missed],
        "quotes_not_found_share": _share(dropped, kept + dropped),
        "risk": {
            "check_risk": _detection(scores, lambda s: s.risk_check),
            "extract": _detection(scores, lambda s: s.risk_extract),
            "either": _detection(scores, lambda s: s.risk_check or s.risk_extract),
            "kinds_check_risk": _kinds(s.risk_check_kinds for s in scores),
            "kinds_extract": _kinds(s.risk_extract_kinds for s in scores),
            "missed_by_both": [
                s.case_id
                for s in scores
                if s.risk_expected and not (s.risk_check or s.risk_extract)
            ],
            "false_alarms": [
                s.case_id
                for s in scores
                if not s.risk_expected and (s.risk_check or s.risk_extract)
            ],
        },
    }


def _detection(scores: Sequence[CaseScore], found: Any) -> dict[str, Any]:
    tp = sum(1 for s in scores if s.risk_expected and found(s))
    fp = sum(1 for s in scores if not s.risk_expected and found(s))
    fn = sum(1 for s in scores if s.risk_expected and not found(s))
    return _prf(tp, fp, fn)


def _kinds(pairs: Any) -> dict[str, Any]:
    tp = fp = fn = 0
    for expected, predicted in pairs:
        tp += len(expected & predicted)
        fp += len(predicted - expected)
        fn += len(expected - predicted)
    return _prf(tp, fp, fn)


def _prf(tp: int, fp: int, fn: int) -> dict[str, Any]:
    precision = _share(tp, tp + fp)
    recall = _share(tp, tp + fn)
    f1 = (
        None
        if precision is None or recall is None or precision + recall == 0
        else round(2 * precision * recall / (precision + recall), 3)
    )
    return {"precision": precision, "recall": recall, "f1": f1, "tp": tp, "fp": fp, "fn": fn}


def _share(part: int, whole: int) -> float | None:
    return None if whole == 0 else round(part / whole, 3)
