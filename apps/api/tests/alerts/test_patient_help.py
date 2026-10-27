"""Данные экрана помощи пациенту из книги правил (спека #57, раздел 7.1).

Экран, номера и правила — выдуманные, из tests/alerts/knowledge.
"""

import shutil
from pathlib import Path

import yaml

from clin_compass.modules.alerts.help import patient_help_view
from clin_compass.modules.alerts.rules import RuleBook, repository_knowledge_dir

KNOWLEDGE = Path(__file__).parent / "knowledge"
GUIDELINE = {
    "source": "test-guideline",
    "title": "Тестовые клинические рекомендации",
    "url": "https://example.org/test-guideline",
}


def test_view_holds_text_contacts_and_source_snapshots() -> None:
    view = patient_help_view(RuleBook.load(KNOWLEDGE))

    assert view is not None
    assert (view.title, view.text, view.checked_on.isoformat()) == (
        "Тест: если очень тяжело",
        "Тест: позвоните 112.",
        "2026-10-01",
    )
    assert [s.model_dump() for s in view.sources] == [{**GUIDELINE, "locator": "раздел 2"}]
    assert [(c.id, c.number, c.label, c.primary) for c in view.contacts] == [
        ("emergency", "112", "Позвонить 112", True),
        ("ambulance", "103", "Скорая помощь 103", False),
    ]
    assert [s.model_dump() for s in view.contacts[1].sources] == [
        {**GUIDELINE, "locator": "раздел 4"}
    ]


def test_triggers_are_reviewed_alert_rules_only() -> None:
    # Маршрутизация (`routing`) экран не открывает, черновик `test-draft` не исполняется.
    view = patient_help_view(RuleBook.load(KNOWLEDGE))

    assert view is not None
    assert [(t.rule_id, t.rule_version) for t in view.triggers] == [("test-self-harm", 1)]
    assert view.triggers[0].when.model_dump(exclude_none=True) == {
        "any": ({"scale": "test-mood", "item": "q3", "op": ">=", "value": 1},)
    }


def test_no_view_without_the_file(tmp_path: Path) -> None:
    root = tmp_path / "knowledge"
    shutil.copytree(KNOWLEDGE, root)
    (root / "patient-help.yaml").unlink()

    assert patient_help_view(RuleBook.load(root)) is None


def test_no_view_for_a_draft(tmp_path: Path) -> None:
    root = tmp_path / "knowledge"
    shutil.copytree(KNOWLEDGE, root)
    path = root / "patient-help.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    path.write_text(yaml.safe_dump({**data, "status": "draft"}), encoding="utf-8")

    assert patient_help_view(RuleBook.load(root)) is None


def test_repository_help_opens_on_phq9_item_9() -> None:
    # Настоящие данные: экран открывает правило пункта 9 PHQ-9, правило врача (HAM-D)
    # пациенту не встречается — его шкалу пациент не заполняет.
    view = patient_help_view(RuleBook.load(repository_knowledge_dir()))

    assert view is not None
    assert {t.rule_id for t in view.triggers} == {
        "phq9-suicidal-thoughts",
        "hamd17-suicidal-intent",
    }
    assert [c.number for c in view.contacts if c.primary] == ["112"]
