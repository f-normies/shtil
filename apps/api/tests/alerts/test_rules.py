"""Книга правил alerts: исполняются только правила `reviewed` (спека #12, раздел 3).

Правила и шкалы — выдуманные, из tests/alerts/knowledge.
"""

from pathlib import Path

import pytest
from clin_compass_knowledge.loader import KnowledgeError
from clin_compass_knowledge.schemas.rule import Condition
from structlog.testing import capture_logs

from clin_compass.modules.alerts.rules import RuleBook

KNOWLEDGE = Path(__file__).parent / "knowledge"


def book() -> RuleBook:
    return RuleBook.load(KNOWLEDGE)


def test_reviewed_rule_fires_with_evidence_and_source_snapshot() -> None:
    fired = book().fire({"test-mood": {"q1": 0, "q2": 0, "q3": 2, "total": 2}}, "test-mood")

    assert [f.rule.id for f in fired] == ["test-self-harm"]
    # В evidence — только значения из условия правила.
    assert fired[0].evidence == {"test-mood": {"q3": 2}}
    assert [s.model_dump() for s in fired[0].sources] == [
        {
            "source": "test-guideline",
            "locator": "раздел 1",
            "title": "Тестовые клинические рекомендации",
            "url": "https://example.org/test-guideline",
        }
    ]


def test_draft_rule_never_fires() -> None:
    # Условие черновика `q1 >= 0` истинно для любого ответа.
    fired = book().fire({"test-mood": {"q1": 3, "q2": 0, "q3": 0, "total": 3}}, "test-mood")

    assert fired == []


def test_rule_over_two_scales_fires_when_results_hold_both() -> None:
    results = {"test-mood": {"q3": 0, "total": 5}, "test-anxiety": {"total": 4}}

    fired = {f.rule.id: f for f in book().fire(results, "test-mood")}

    assert set(fired) == {"test-high-mood", "test-mood-and-anxiety"}
    assert fired["test-mood-and-anxiety"].evidence == {
        "test-mood": {"total": 5},
        "test-anxiety": {"total": 4},
    }


def test_missing_scale_gives_no_signal() -> None:
    assert book().fire({}, "test-mood") == []


def test_only_rules_over_the_submitted_scale_run() -> None:
    # Условие test-self-harm истинно, но его шкалу сейчас не отправляли.
    results = {"test-mood": {"q3": 2, "total": 2}, "test-anxiety": {"total": 1}}

    assert book().fire(results, "test-anxiety") == []


def test_rule_with_any_over_two_scales_ignores_old_answer_of_the_other_scale() -> None:
    self_harm = next(r for r in book().rules if r.id == "test-self-harm")
    any_rule = self_harm.model_copy(
        update={
            "id": "test-mood-or-anxiety",
            "when": Condition.model_validate(
                {
                    "any": [
                        {"scale": "test-mood", "item": "q3", "op": ">=", "value": 1},
                        {"scale": "test-anxiety", "score": "total", "op": ">=", "value": 4},
                    ]
                }
            ),
        }
    )
    rules = RuleBook([any_rule], book().sources)
    # Высокий балл тревоги — старый ответ. Сейчас пришла шкала настроения без самоповреждения.
    results = {"test-mood": {"q3": 0, "total": 0}, "test-anxiety": {"total": 5}}

    assert rules.fire(results, "test-mood") == []
    [fired] = rules.fire(results, "test-anxiety")
    assert fired.rule.id == "test-mood-or-anxiety"
    # Спека #95: в evidence — только сработавшая ветка `any`. Иначе «Причина» показала бы
    # врачу балл настроения, который правило не нарушил.
    assert fired.evidence == {"test-anxiety": {"total": 5}}


def test_load_logs_how_many_rules_will_run() -> None:
    with capture_logs() as logs:
        RuleBook.load(KNOWLEDGE)

    assert logs == [
        {
            "event": "alerts.rules_loaded",
            "log_level": "info",
            "root": str(KNOWLEDGE),
            "reviewed": 3,
            "total": 4,
        }
    ]


def test_missing_knowledge_dir_stops_start(tmp_path: Path) -> None:
    # Без манифеста источников каталог не похож на knowledge/: правил было бы ноль без ошибки.
    with pytest.raises(KnowledgeError, match=r"sources/manifest\.yaml: файла нет"):
        RuleBook.load(tmp_path)
    with pytest.raises(KnowledgeError, match=r"sources/manifest\.yaml: файла нет"):
        RuleBook.load(tmp_path / "missing")
