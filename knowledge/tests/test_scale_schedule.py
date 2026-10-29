"""Расписание шкал: `scale-schedule.yaml` (спека #111)."""

from pathlib import Path
from typing import Any

import pytest

from clin_compass_knowledge.loader import load_knowledge
from tests.helpers import VALID, Edit, messages

FILE = "scale-schedule.yaml"


def test_scale_schedule_is_loaded() -> None:
    schedule = load_knowledge(VALID).scale_schedule

    assert schedule is not None
    assert schedule.grace_days == 3
    assert [(s.scale, s.period_days) for s in schedule.scales] == [("example-mood", 14)]
    assert schedule.allowed_period_days == (7, 14)


def test_missing_file_is_not_an_error(tree: Path) -> None:
    # Мини-каталоги knowledge/ в тестах API обходятся без этого файла.
    (tree / FILE).unlink()

    assert messages(tree) == []
    assert load_knowledge(tree).scale_schedule is None


def test_empty_file_is_reported(tree: Path) -> None:
    (tree / FILE).write_text("", encoding="utf-8")

    assert messages(tree) == [f"{FILE}: файл пустой"]


def test_scale_of_clinician_is_rejected(tree: Path, edit: Edit) -> None:
    # Спека #111, «Данные»: по расписанию опрашивается пациент, шкалы врача — #94.
    edit("scales/example-mood.yaml", lambda d: d.update(respondent="clinician"))

    assert f"{FILE}: scales[0].scale: шкалу example-mood заполняет не пациент" in messages(tree)


# (правка данных, ожидаемая ошибка). Источник случая — спека #111, раздел «Данные».
CASES: list[tuple[str, Any, str]] = [
    (
        "period_positive",
        lambda d: d["scales"][0].update(period_days=0),
        "scales[0].period_days: нужно не меньше 1",
    ),
    (
        "grace_not_negative",
        lambda d: d.update(grace_days=-1),
        "grace_days: нужно не меньше 0",
    ),
    (
        "grace_shorter_than_period",
        lambda d: d["scales"][0].update(period_days=3),
        "scales[0].period_days: 3 не больше срока пропуска grace_days 3",
    ),
    (
        "scale_exists",
        lambda d: d["scales"][0].update(scale="nope"),
        "scales[0].scale: нет шкалы nope",
    ),
    (
        "scale_listed_once",
        lambda d: d["scales"].append(dict(d["scales"][0])),
        "scales[1].scale: повторяется example-mood",
    ),
    (
        "source_in_manifest",
        lambda d: d["scales"][0]["source"].update(source="nope"),
        "scales[0].source.source: нет источника nope в манифесте",
    ),
    (
        "source_is_primary",
        lambda d: d["scales"][0]["source"].update(source="example-international"),
        "scales[0].source: только дополнительные источники: нужен основной",
    ),
    (
        "grace_basis_required",
        lambda d: d.pop("grace_basis"),
        "grace_basis: обязательное поле",
    ),
    # Спека #180, «Данные knowledge/»: частоту врач выбирает из допустимых периодов.
    (
        "allowed_period_longer_than_grace",
        lambda d: d.update(allowed_period_days=[3, 14]),
        "allowed_period_days[0]: 3 не больше срока пропуска grace_days 3",
    ),
    (
        "allowed_period_listed_once",
        lambda d: d.update(allowed_period_days=[7, 14, 7]),
        "allowed_period_days[2]: повторяется 7",
    ),
    (
        "default_period_is_allowed",
        lambda d: d["scales"][0].update(period_days=10),
        "scales[0].period_days: 10 нет среди allowed_period_days",
    ),
    (
        "allowed_period_basis_required",
        lambda d: d.pop("allowed_period_basis"),
        "allowed_period_basis: обязательное поле",
    ),
    (
        "extra_field_is_reported",
        lambda d: d["scales"][0].update(weekday="wed"),
        "scales[0].weekday: лишнее поле",
    ),
]


@pytest.mark.parametrize(("change", "message"), [c[1:] for c in CASES], ids=[c[0] for c in CASES])
def test_scale_schedule_check(tree: Path, edit: Edit, change: Any, message: str) -> None:
    edit(FILE, change)

    assert messages(tree) == [f"{FILE}: {message}"]
