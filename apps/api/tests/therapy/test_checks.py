"""Проверки препарата по профилю пациента без БД (спека #28, раздел 5; спека #29, раздел 6).

Препарат — выдуманный `test-drug` из `tests/therapy/knowledge`: показание F41.1 и курс
по F43.2 с обязательным U09.9, возраст от 18 лет, схема 10 мг 2 раза в сутки.
"""

from datetime import date
from pathlib import Path

import pytest
from clin_compass_knowledge.safety import Medication

from clin_compass.modules.therapy.checks import (
    Catalog,
    Check,
    Profile,
    Regimen,
    age_on,
    can_propose,
    candidates,
    is_off_label,
    run_checks,
)

KNOWLEDGE = Path(__file__).parent / "knowledge"
CATALOG = Catalog.load(KNOWLEDGE)
DRUG = CATALOG.drugs["test-drug"]
TODAY = date(2026, 10, 18)
ADULT = date(1992, 5, 1)
EGFR_OK = {"egfr": 95}


def profile(
    *codes: str,
    birth: date | None = ADULT,
    since: date | None = None,
    values: dict[str, object] | None = None,
    medications: tuple[Medication, ...] = (),
) -> Profile:
    prescribed = {"test-drug": since} if since else {}
    return Profile(
        birth_date=birth,
        diagnoses=frozenset(codes),
        prescribed_since=prescribed,
        sex="female",
        values=EGFR_OK if values is None else values,
        medications=medications,
    )


def by_id(p: Profile) -> dict[str, Check]:
    [(drug, indication)] = candidates(CATALOG, p)
    return {c.id: c for c in run_checks(drug, indication, p, CATALOG, TODAY)}


def statuses(codes: tuple[str, ...], **kwargs: object) -> dict[str, str]:
    return {c.id: c.status for c in by_id(profile(*codes, **kwargs)).values()}  # type: ignore[arg-type]


def stopped(inn: str, days_ago: int) -> Medication:
    day = date.fromordinal(TODAY.toordinal() - days_ago)
    return Medication(name=inn.capitalize(), inn=inn, started_on=None, stopped_on=day)


def test_only_reviewed_drugs_are_in_the_catalog() -> None:
    assert set(CATALOG.drugs) == {"test-drug"}


def test_gad_adult_passes_everything() -> None:
    assert statuses(("F41.1",)) == {
        "registered": "pass",
        "not_prescribed": "pass",
        "indication": "pass",
        "age": "pass",
        "contra:maoi": "pass",
        "contra:renal": "pass",
        "caution:serotonergic": "pass",
    }


def test_maoi_stopped_10_days_ago_blocks_and_names_the_first_allowed_day() -> None:
    # Сценарий 3, шаг s5: моклобемид отменён 10 дней назад, срок 2 недели.
    checks = by_id(profile("F41.1", medications=(stopped("моклобемид", 10),)))
    maoi = checks["contra:maoi"]

    # Отменён 08.10; 22.10 — 14-й день, ещё в сроке (ревью медика #76).
    assert maoi.status == "fail"
    assert "можно назначать с 23.10.2026" in maoi.detail
    assert maoi.actions == ("Не назначать до окончания срока",)
    assert not can_propose(list(checks.values()))


def test_maoi_stopped_16_days_ago_passes() -> None:
    # Сценарий 3, шаги s8–s9.
    checks = by_id(profile("F41.1", medications=(stopped("моклобемид", 16),)))

    assert checks["contra:maoi"].status == "pass"


def test_triptan_warns_but_allows_a_proposal() -> None:
    # Сценарий 2, шаг s5: суматриптан — «желательно», противопоказанием не является.
    taking = Medication(name="Суматриптан", inn="суматриптан", started_on=None, stopped_on=None)
    checks = by_id(profile("F41.1", medications=(taking,)))

    assert checks["caution:serotonergic"].status == "warn"
    assert can_propose(list(checks.values()))


def test_unknown_egfr_asks_the_doctor() -> None:
    assert by_id(profile("F41.1", values={}))["contra:renal"].status == "manual"


def test_medication_without_inn_asks_the_doctor() -> None:
    unknown = Medication(name="Неизвестин", inn=None, started_on=None, stopped_on=None)
    checks = by_id(profile("F41.1", medications=(unknown,)))
    [check] = [c for c in checks.values() if c.id.startswith("unmatched:")]

    assert check.status == "manual"
    assert "«Неизвестин» не сопоставлен с МНН" in check.title


@pytest.mark.parametrize(("days_ago", "checked"), [(14, True), (15, False)])
def test_stopped_medication_without_inn_is_checked_within_the_maoi_window(
    days_ago: int, checked: bool
) -> None:
    # Без МНН это может быть ингибитор МАО: важен, пока идёт срок «2 недели после отмены».
    gone = Medication(
        name="Неизвестин",
        inn=None,
        started_on=None,
        stopped_on=date.fromordinal(TODAY.toordinal() - days_ago),
    )
    checks = by_id(profile("F41.1", medications=(gone,)))

    assert ("unmatched:неизвестин" in checks) is checked


def test_check_ids_are_unique_when_a_medication_is_entered_twice() -> None:
    # Ревью #76: отменённый и снова внесённый препарат без МНН давал два одинаковых id.
    gone = Medication(
        name="Неизвестин",
        inn=None,
        started_on=None,
        stopped_on=date.fromordinal(TODAY.toordinal() - 5),
    )
    again = Medication(name="неизвестин ", inn=None, started_on=None, stopped_on=None)
    p = profile("F41.1", medications=(gone, again))
    [(drug, indication)] = candidates(CATALOG, p)

    ids = [c.id for c in run_checks(drug, indication, p, CATALOG, TODAY)]

    assert len(ids) == len(set(ids))
    assert ids.count("unmatched:неизвестин") == 1


def test_medication_without_inn_check_is_named_after_the_medication() -> None:
    # По id повторная проверка отличает новый препарат без МНН от известного с назначения.
    first = Medication(name="Неизвестин", inn=None, started_on=None, stopped_on=None)
    second = Medication(name="Другин", inn=None, started_on=None, stopped_on=None)

    only = by_id(profile("F41.1", medications=(first,)))
    both = by_id(profile("F41.1", medications=(second, first)))

    assert {i for i in only if i.startswith("unmatched:")} == {"unmatched:неизвестин"}
    assert {i for i in both if i.startswith("unmatched:")} == {
        "unmatched:неизвестин",
        "unmatched:другин",
    }


@pytest.mark.parametrize(
    ("birth", "text"),
    [(date(1992, 5, 1), "34 года"), (date(2005, 1, 1), "21 год"), (date(1981, 1, 1), "45 лет")],
)
def test_age_is_worded_in_russian(birth: date, text: str) -> None:
    p = profile("F41.1", birth=birth)
    [(drug, indication)] = candidates(CATALOG, p)
    [age] = [c for c in run_checks(drug, indication, p, CATALOG, TODAY) if c.id == "age"]

    assert age.detail == text


def test_minor_fails_age() -> None:
    assert statuses(("F41.1",), birth=date(2009, 1, 1))["age"] == "fail"


def test_unknown_birth_date_asks_the_doctor() -> None:
    assert statuses(("F41.1",), birth=None)["age"] == "manual"


def test_course_without_required_code_fails_indication() -> None:
    assert statuses(("F43.2",))["indication"] == "fail"


def test_course_with_required_code_passes_and_asks_about_severity() -> None:
    result = statuses(("F43.2", "U09.9"))

    assert result["indication"] == "pass"
    assert result["manual-0"] == "manual"


def test_already_prescribed_fails() -> None:
    p = profile("F41.1", since=date(2026, 10, 1))
    [(drug, indication)] = candidates(CATALOG, p)
    checks = run_checks(drug, indication, p, CATALOG, TODAY)

    prescribed = next(c for c in checks if c.id == "not_prescribed")
    assert prescribed.status == "fail"
    assert "01.10.2026" in prescribed.detail
    assert not can_propose(checks)


def test_manual_checks_do_not_block_a_proposal() -> None:
    p = profile("F43.2", "U09.9")
    [(drug, indication)] = candidates(CATALOG, p)

    assert can_propose(run_checks(drug, indication, p, CATALOG, TODAY))


def test_no_matching_diagnosis_gives_no_candidates() -> None:
    assert candidates(CATALOG, profile("F32.1")) == []
    assert candidates(CATALOG, profile("U09.9")) == []


def test_every_clinical_check_has_a_source() -> None:
    # «Сейчас не назначен» — проверка данных системы, а не правило инструкции.
    p = profile("F43.2", "U09.9")
    [(drug, indication)] = candidates(CATALOG, p)
    run = run_checks(drug, indication, p, CATALOG, TODAY)

    assert all(c.source is not None for c in run if c.id != "not_prescribed")
    assert all(c.source is not None for c in run if c.id.startswith(("contra:", "caution:")))
    assert next(c for c in run if c.id == "not_prescribed").source is None


@pytest.mark.parametrize(
    ("birth", "today", "years"),
    [
        (date(2008, 10, 18), date(2026, 10, 18), 18),
        (date(2008, 10, 19), date(2026, 10, 18), 17),
        (date(2008, 2, 29), date(2026, 2, 28), 17),
        (date(2008, 2, 29), date(2026, 3, 1), 18),
    ],
)
def test_age_turns_on_the_birthday(birth: date, today: date, years: int) -> None:
    assert age_on(birth, today) == years


GAD = DRUG.indications[0]
COURSE = DRUG.indications[1]


@pytest.mark.parametrize(
    ("indication", "regimen", "off_label"),
    [
        (GAD, Regimen(10, 2, None), False),
        (GAD, Regimen(10, 2, 90), False),  # длительность определяет врач
        (GAD, Regimen(20, 2, None), True),
        (GAD, Regimen(10, 3, None), True),
        (COURSE, Regimen(10, 2, 28), False),
        (COURSE, Regimen(10, 2, 42), False),
        (COURSE, Regimen(10, 2, 43), True),
        (COURSE, Regimen(10, 2, 14), True),
        (COURSE, Regimen(10, 2, None), True),
    ],
)
def test_off_label_regimen(indication: object, regimen: Regimen, off_label: bool) -> None:
    assert is_off_label(indication, regimen) is off_label  # type: ignore[arg-type]


def test_label_regimen_of_an_indication() -> None:
    assert Regimen.of(COURSE) == Regimen(10, 2, 28)
    assert Regimen.of(GAD) == Regimen(10, 2, None)
