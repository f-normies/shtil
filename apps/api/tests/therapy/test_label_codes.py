"""Коды МКБ-10 показаний «Авиандра» и проверка `indication` (спека #28, раздел 5).

Коды показаний в `knowledge/drugs/aviandr.yaml` сопоставил медик: в ОХЛП их нет. Код
пациента совпадает с показанием, только если он есть в карточке. Подрубрика кода из карточки —
не совпадение: проверка `fail`, предложения нет. Справочник — штатная загрузка `knowledge/`:
в него входят только карточки `reviewed` (#82).
"""

from datetime import date
from pathlib import Path

import clin_compass_knowledge
import pytest
from clin_compass_knowledge.loader import load_knowledge

from clin_compass.modules.therapy.checks import (
    Catalog,
    Profile,
    can_propose,
    candidates,
    run_checks,
)

KNOWLEDGE = load_knowledge(Path(clin_compass_knowledge.__file__).resolve().parents[2])
CATALOG = Catalog.from_knowledge(KNOWLEDGE)
AVIANDR = CATALOG.drugs["aviandr"]
TODAY = date(2026, 10, 19)
ADULT = date(1992, 5, 1)
POST_COVID = "post-covid-anxiety"


def indication_check(codes: tuple[str, ...], indication_id: str) -> tuple[str, str, bool]:
    """Статус и текст проверки `indication` и можно ли предложить препарат."""
    profile = Profile(birth_date=ADULT, diagnoses=frozenset(codes))
    found = {i.id: i for _, i in candidates(CATALOG, profile)}
    run = run_checks(AVIANDR, found[indication_id], profile, CATALOG, TODAY)
    check = next(c for c in run if c.id == "indication")
    return check.status, check.detail, can_propose(run)


@pytest.mark.parametrize(
    ("codes", "indication_id", "status", "source"),
    [
        (("F41.1",), "gad", "pass", "ОХЛП 4.1: генерализованное тревожное расстройство"),
        (
            ("F43.2", "U09.9"),
            POST_COVID,
            "pass",
            "ОХЛП 4.1: тревожные состояния после COVID-19 … с нарушением адаптации",
        ),
        (
            ("F43.8", "U09.9"),
            POST_COVID,
            "pass",
            "ОХЛП 4.1: тревожные состояния после COVID-19 у пациентов со стрессовой реакцией",
        ),
        (
            ("F43.22", "U09.9"),
            POST_COVID,
            "fail",
            "ОХЛП 4.1: вид расстройства адаптации не назван; подрубрики в карточке нет",
        ),
    ],
)
def test_indication_matches_only_codes_from_the_card(
    codes: tuple[str, ...], indication_id: str, status: str, source: str
) -> None:
    assert indication_check(codes, indication_id)[0] == status, source


def test_subcode_is_a_failed_check_with_the_label_codes() -> None:
    # Без этого врач с кодом F43.22 видел «в справочнике нет препарата» — ложный ответ.
    status, detail, proposable = indication_check(("F43.22", "U09.9"), POST_COVID)

    assert status == "fail"
    assert detail == (
        "Код F43.22 не указан в показаниях инструкции (указаны: F43.2, F43.8). "
        "Уточните код диагноза"
    )
    assert not proposable


@pytest.mark.parametrize(
    ("code", "source"),
    [
        ("F43.1", "ОХЛП 4.1 не называет ПТСР; соседняя рубрика, а не подрубрика F43.2 или F43.8"),
        ("F43.0", "острая реакция на стресс проходит за часы или дни, курс — 4–6 недель (#28)"),
    ],
)
def test_sibling_code_is_not_a_candidate(code: str, source: str) -> None:
    profile = Profile(birth_date=ADULT, diagnoses=frozenset({code, "U09.9"}))

    assert candidates(CATALOG, profile) == [], source
