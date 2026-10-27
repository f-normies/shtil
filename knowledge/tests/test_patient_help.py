"""Экран помощи пациенту: `patient-help.yaml` (спека #57, раздел 4)."""

import datetime as dt
from pathlib import Path
from typing import Any

import pytest

from clin_compass_knowledge.loader import load_knowledge
from tests.helpers import VALID, Edit, messages

FILE = "patient-help.yaml"


def test_patient_help_is_loaded() -> None:
    help_ = load_knowledge(VALID).patient_help

    assert help_ is not None
    assert [(c.id, c.number, c.primary) for c in help_.contacts] == [
        ("emergency", "112", True),
        ("ambulance", "103", False),
    ]
    assert help_.checked_on == dt.date(2026, 10, 1)


def test_missing_file_is_not_an_error(tree: Path) -> None:
    # Мини-каталоги knowledge/ в тестах API обходятся без этого файла.
    (tree / FILE).unlink()

    assert messages(tree) == []
    assert load_knowledge(tree).patient_help is None


def test_checked_in_the_future_is_rejected(tree: Path, edit: Edit) -> None:
    tomorrow = dt.date.today() + dt.timedelta(days=1)
    edit(FILE, lambda d: d.update(checked_on=tomorrow))

    assert messages(tree) == [f"{FILE}: checked_on: дата проверки {tomorrow} ещё не наступила"]


# (правка данных, ожидаемая ошибка). Источник случая — спека #57, раздел 4.
CASES: list[tuple[str, Any, str]] = [
    (
        "number_digits_only",
        lambda d: d["contacts"][0].update(number="+7 112"),
        "contacts[0].number: не подходит под формат ^[0-9]{2,15}$",
    ),
    (
        "one_primary",
        lambda d: d["contacts"][1].update(primary=True),
        "contacts: нужен ровно один контакт с primary: true, найдено 2",
    ),
    (
        "no_primary",
        lambda d: d["contacts"][0].update(primary=False),
        "contacts: нужен ровно один контакт с primary: true, найдено 0",
    ),
    (
        "contact_id_unique",
        lambda d: d["contacts"][1].update(id="emergency"),
        "contacts[1].id: повторяется emergency",
    ),
    (
        "text_source_in_manifest",
        lambda d: d["sources"][0].update(source="nope"),
        "sources[0].source: нет источника nope в манифесте",
    ),
    (
        "contact_source_in_manifest",
        lambda d: d["contacts"][1]["sources"][0].update(source="nope"),
        "contacts[1].sources[0].source: нет источника nope в манифесте",
    ),
    (
        "text_source_is_primary",
        lambda d: d["sources"][0].update(source="example-international"),
        "sources: только дополнительные источники: нужен основной",
    ),
    (
        "contact_source_is_primary",
        lambda d: d["contacts"][0]["sources"][0].update(source="example-international"),
        "contacts[0].sources: только дополнительные источники: нужен основной",
    ),
    (
        "contact_needs_source",
        lambda d: d["contacts"][0].update(sources=[]),
        "contacts[0].sources: минимум элементов в списке — 1, сейчас 0",
    ),
    (
        "extra_field_is_reported",
        lambda d: d["contacts"][0].update(region="msk"),
        "contacts[0].region: лишнее поле",
    ),
]


@pytest.mark.parametrize(("change", "message"), [c[1:] for c in CASES], ids=[c[0] for c in CASES])
def test_patient_help_check(tree: Path, edit: Edit, change: Any, message: str) -> None:
    edit(FILE, change)

    assert messages(tree) == [f"{FILE}: {message}"]
