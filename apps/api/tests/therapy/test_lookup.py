"""МНН препарата вне системы по снимку ГРЛС без БД (спека #29, раздел 5).

Снимок — `tests/therapy/knowledge/sources/files/registry.csv`: у «Маоина» два удостоверения
с МНН моклобемид, у «Безмннина» МНН не заполнен, у «Двоина» два разных МНН.
"""

from pathlib import Path

import pytest

from clin_compass.modules.therapy import safety
from clin_compass.modules.therapy.checks import Catalog

CATALOG = Catalog.load(Path(__file__).parent / "knowledge")


@pytest.mark.parametrize(
    ("name", "inn"),
    [
        # Ингибитор МАО под торговым названием с двумя удостоверениями.
        ("Маоин", "Моклобемид"),
        ("Аурорикс", "Моклобемид"),
        # Пустой МНН снимка — не МНН: подбор попросит врача проверить взаимодействия.
        ("Безмннин", None),
        ("Аурорик", None),
        ("Неизвестин", None),
    ],
)
def test_lookup_gives_inn_of_an_exact_match(name: str, inn: str | None) -> None:
    assert safety.lookup(CATALOG, name).inn == inn


def test_lookup_offers_every_inn_when_registrations_differ() -> None:
    # Ревью #76: список выбора МНН повторял варианты и обрезался до трёх.
    found = safety.lookup(CATALOG, "Двоин")

    assert (found.inn, found.inn_options) == (None, ["Первин", "Вторин"])
