"""Решения по сигналам в knowledge/ этого репозитория (спека #93)."""

from clin_compass_knowledge.loader import load_knowledge
from tests.helpers import REPO_KNOWLEDGE

KNOWLEDGE = load_knowledge(REPO_KNOWLEDGE)


def test_repository_has_reviewed_signal_decisions() -> None:
    # Загрузчик отсутствие файла пропускает; в репозитории он обязателен (#95, #35).
    decisions = KNOWLEDGE.signal_decisions

    assert decisions is not None
    assert decisions.status == "reviewed"


def test_c_ssrs_is_not_mentioned() -> None:
    # Ответ медика в #93, пункт 6: шкалы в системе нет, ни в одном тексте данных её не называем.
    files = [p for p in REPO_KNOWLEDGE.rglob("*.yaml") if "tests" not in p.parts]

    assert [p.name for p in files if "C-SSRS" in p.read_text(encoding="utf-8")] == []


def test_therapy_decisions_follow_the_adverse_event_form() -> None:
    # Спека #95, раздел 3: поле «Предпринятые меры» извещения (приказ № 3518, прил. 1).
    # При противопоказании продолжить лечение нельзя: источника для этого нет.
    decisions = KNOWLEDGE.signal_decisions
    assert decisions is not None
    by_kind = {
        kind: [d.id for d in decisions.therapy.decisions if kind in d.applies_to]
        for kind in ("contraindication", "caution", "reaction")
    }

    assert by_kind == {
        "contraindication": ["drug-stopped", "data-corrected"],
        "caution": ["drug-stopped", "dose-reduced", "continued-monitoring"],
        "reaction": ["drug-stopped", "dose-reduced", "reaction-treated", "continued-monitoring"],
    }
    sourced = [d for d in decisions.therapy.decisions if d.id != "data-corrected"]
    assert all(any(r.source == "rzn-pharmacovigilance-2024" for r in d.sources) for d in sourced)
