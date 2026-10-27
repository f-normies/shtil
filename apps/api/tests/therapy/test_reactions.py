"""Отметка нежелательной реакции и черновик извещения без БД (спека #29, разделы 3, 6 и 8).

Препарат — выдуманный `test-drug` из `tests/therapy/knowledge`: «скованность мышц» уровня
«обязательно», «головная боль» — «желательно», «повышение КФК» пациент не отмечает.
"""

from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from clin_compass.modules.therapy import views
from clin_compass.modules.therapy.checks import Catalog
from clin_compass.modules.therapy.reactions import (
    Draft,
    UnknownReactionError,
    concomitant,
    draft_text,
    level_of,
    pick,
    signal_detail,
    signal_title,
)
from clin_compass.modules.therapy.schemas import MedicationOut, ReactionReportOut, ReportedReaction

CATALOG = Catalog.load(Path(__file__).parent / "knowledge")
DRUG = CATALOG.drugs["test-drug"]


def test_patient_sees_only_reportable_reactions_in_their_words() -> None:
    options = views.reaction_options(DRUG)

    assert [(o.id, o.title) for o in options] == [
        ("stiffness", "скованность мышц"),
        ("headache", "болит голова"),
    ]


def test_no_options_without_doctor_actions() -> None:
    # Без `reaction_actions` отметку не принять (409), поэтому пациенту её не предлагаем.
    drug = DRUG.model_copy(update={"reaction_actions": None})

    assert views.reaction_options(drug) == []


def test_pick_keeps_order_and_drops_repeats() -> None:
    chosen = pick(DRUG, ["headache", "stiffness", "headache"])

    assert [r.id for r in chosen] == ["headache", "stiffness"]


@pytest.mark.parametrize("reaction_id", ["no-such-reaction", "ck-up"])
def test_pick_rejects_reactions_the_patient_cannot_report(reaction_id: str) -> None:
    # Реакции нет в перечне инструкции или это лабораторный показатель.
    with pytest.raises(UnknownReactionError):
        pick(DRUG, ["headache", reaction_id])


@pytest.mark.parametrize(
    ("ids", "level"),
    [
        (["headache"], "desirable"),
        (["stiffness"], "mandatory"),
        # Наибольший уровень среди отмеченных: спека #29, раздел 6.
        (["headache", "stiffness"], "mandatory"),
    ],
)
def test_signal_level_is_the_highest_reported(ids: list[str], level: str) -> None:
    assert level_of(pick(DRUG, ids)) == level


def test_signal_title_names_drug_and_reactions() -> None:
    title = signal_title("Тестин", pick(DRUG, ["stiffness", "headache"]))

    assert title == "Нежелательная реакция на «Тестин»: скованность мышц, головная боль"


def test_signal_detail_has_dates_day_of_course_and_patient_text() -> None:
    detail = signal_detail(
        pick(DRUG, ["stiffness"]),
        reported_on=date(2026, 10, 20),
        started_on=date(2026, 10, 11),
        note="с утра сводит челюсть",
    )

    assert detail == (
        "Отмечено 20.10.2026, 10-й день приёма (с 11.10.2026): скованность мышц."
        " Пациент пишет: «с утра сводит челюсть»"
    )


def test_signal_detail_without_note() -> None:
    detail = signal_detail(
        pick(DRUG, ["headache"]),
        reported_on=date(2026, 10, 11),
        started_on=date(2026, 10, 11),
        note=None,
    )

    assert detail == "Отмечено 11.10.2026, 1-й день приёма (с 11.10.2026): головная боль."


def medication(name: str, started: date | None, stopped: date | None) -> MedicationOut:
    return MedicationOut(
        id=uuid4(),
        name=name,
        inn=name.lower(),
        registry_check="registered",
        started_on=started,
        stopped_on=stopped,
        entered_by=uuid4(),
        created_at=datetime(2026, 10, 1, tzinfo=UTC),
    )


def test_concomitant_medications_are_those_taken_on_the_date() -> None:
    on = date(2026, 10, 20)
    taken = [
        medication("Суматриптан", date(2026, 1, 1), None),
        medication("Без дат", None, None),
        medication("Отменён в тот же день", None, on),
    ]
    not_taken = [
        medication("Отменён раньше", None, date(2026, 10, 19)),
        medication("Начат позже", date(2026, 10, 21), None),
    ]

    assert concomitant([*taken, *not_taken], on) == taken


def report(at: datetime, ids: list[str], note: str | None) -> ReactionReportOut:
    return ReactionReportOut(
        id=uuid4(),
        prescription_id=uuid4(),
        reactions=[
            ReportedReaction(
                id=r.id, title=r.title, patient_title=r.patient_title or r.title, level=r.level
            )
            for r in pick(DRUG, ids)
        ],
        note=note,
        reported_at=at,
    )


def draft(**changes: object) -> Draft:
    fields: dict[str, object] = {
        "trade_name": "Тестин",
        "inn": "тестин",
        "reg_number": "ЛП-000100",
        "additional_monitoring": True,
        "indication_title": "Тревожное расстройство",
        "dose_mg": 10.0,
        "times_per_day": 2,
        "schedule": "утром и вечером",
        "started_on": date(2026, 10, 11),
        "stopped_on": date(2026, 10, 21),
        "stop_reason": "Нежелательная реакция",
        "course_end": None,
        "off_label_reason": None,
        # 23:30 UTC 19.10 — уже 20.10 по Москве.
        "reports": [
            report(datetime(2026, 10, 19, 23, 30, tzinfo=UTC), ["stiffness"], "сводит челюсть"),
            report(datetime(2026, 10, 20, 9, tzinfo=UTC), ["headache"], None),
        ],
        "medications": [
            medication("Суматриптан", date(2026, 1, 1), None),
            medication("Отменён раньше", None, date(2026, 10, 1)),
        ],
        "sex": "male",
        "birth_date": date(1980, 5, 1),
        "doctor_name": "Врачев Иван Иванович",
        "today": date(2026, 10, 21),
    }
    fields.update(changes)
    return Draft(**fields)  # type: ignore[arg-type]


def test_draft_has_drug_dates_reactions_and_concomitant_medications() -> None:
    text = draft_text(draft())

    for line in [
        "Пациент: мужской пол, 46 лет",
        "Подозреваемый препарат: Тестин (тестин), РУ ЛП-000100",
        "▼ Препарат подлежит дополнительному мониторингу",
        "Показание: Тревожное расстройство",
        "Доза и режим: 10 мг 2 раза в сутки, утром и вечером",
        "Начало приёма: 11.10.2026",
        "Отмена: 21.10.2026, причина: Нежелательная реакция",
        "- 20.10.2026: скованность мышц. Со слов пациента: «сводит челюсть»",
        "- 20.10.2026: головная боль",
        "Сопутствующие препараты на 20.10.2026:",
        "- Суматриптан (суматриптан), с 01.01.2026",
        "Тяжесть: ______ (заполняет врач)",
        "Исход: ______ (заполняет врач)",
        "Врач: Врачев Иван Иванович",
    ]:
        assert line in text.splitlines(), line
    assert "Отменён раньше" not in text


def test_draft_of_an_active_prescription_without_profile_or_monitoring() -> None:
    text = draft_text(
        draft(
            stopped_on=None,
            stop_reason=None,
            sex=None,
            birth_date=None,
            reg_number=None,
            additional_monitoring=False,
            medications=[],
            off_label_reason="Решение врача по тяжести состояния",
        )
    ).splitlines()

    assert "Пациент: пол не указан, возраст не указан" in text
    assert "Подозреваемый препарат: Тестин (тестин)" in text
    assert "Отмена: препарат не отменён" in text
    assert "Вне инструкции, обоснование врача: Решение врача по тяжести состояния" in text
    assert "- нет данных о других препаратах" in text
    assert not [line for line in text if line.startswith("▼")]


@pytest.mark.parametrize(
    ("times", "expected"),
    [(1, "1 раз"), (2, "2 раза"), (4, "4 раза"), (5, "5 раз"), (12, "12 раз")],
)
def test_draft_declines_times_per_day(times: int, expected: str) -> None:
    text = draft_text(draft(times_per_day=times)).splitlines()

    assert f"Доза и режим: 10 мг {expected} в сутки, утром и вечером" in text


def test_draft_of_a_completed_course_names_its_last_day() -> None:
    # Курс 11.10–20.10 закончился сам: `ends_on` — день после последнего приёма (#28).
    text = draft_text(
        draft(stopped_on=None, stop_reason=None, course_end=date(2026, 10, 21))
    ).splitlines()

    assert "Отмена: курс завершён, последний день приёма 20.10.2026" in text


def test_draft_age_is_taken_on_the_day_of_the_first_reaction() -> None:
    # Первая отметка 20.10.2026, день рождения 21.10: в извещении возраст на момент реакции.
    text = draft_text(draft(birth_date=date(1980, 10, 21))).splitlines()

    assert "Пациент: мужской пол, 45 лет" in text
