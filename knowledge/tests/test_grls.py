"""Сверка названий препаратов со снимком ГРЛС (спека #32, раздел 5.4).

Снимок в фикстуре выдуманный, кроме строки «Авиандра»: её номер и МНН — из выгрузки ГРЛС
от 16.10.2026.
"""

from pathlib import Path

import pytest

from clin_compass_knowledge.grls import DrugMatch, Registry, RegistryEntry, drug_name, load_registry
from tests.helpers import FIXTURES, REPO_KNOWLEDGE

REGISTRY = FIXTURES / "grls" / "registry.csv"
AVIANDR = DrugMatch("АВИАНДР®", "Маритупирдин", "ЛП-№(002445)-(РГ-RU)")
SERTRALINE = DrugMatch(None, "Сертралин", None)


@pytest.fixture(scope="module")
def registry() -> Registry:
    return load_registry(REGISTRY)


@pytest.mark.parametrize(
    ("name", "check", "matches"),
    [
        # Точное совпадение с торговым наименованием: регистр и ® не важны.
        ("Авиандр", "registered", [AVIANDR]),
        ("АВИАНДР", "registered", [AVIANDR]),
        # Дефис и пробел не мешают: «новопассит» — это «Ново-Пассит».
        ("новопассит", "registered", [DrugMatch("Ново-Пассит®", "", "ЛП-000005")]),
        ("Но-шпа", "registered", [DrugMatch("Но-шпа®", "Дротаверин", "ЛП-000009")]),
        # МНН важнее первого слова торгового наименования «Сертралин-Тест».
        ("сертралин", "registered", [SERTRALINE]),
        # Первое слово торгового наименования.
        (
            "пустырник",
            "registered",
            [DrugMatch("ПУСТЫРНИК форте", "Пустырника травы экстракт", "ЛП-000008")],
        ),
        # Доза и лекарственная форма в названии не мешают.
        ("Авиандр 50 мг", "registered", [AVIANDR]),
        ("авиандр 50мг", "registered", [AVIANDR]),
        ("Авиандр таблетки", "registered", [AVIANDR]),
        ("сертралин 50 мг", "registered", [SERTRALINE]),
        # Только общие слова — не название препарата.
        ("таблетки", "not_found", []),
        ("капли", "not_found", []),
        # Опечатка распознавания.
        ("авиандор", "similar", [AVIANDR]),
        # Два близких названия: врач видит оба (оценки 89 и 88).
        (
            "ципролекс",
            "similar",
            [
                DrugMatch("Ципралекс®", "Эсциталопрам", "ЛП-000003"),
                DrugMatch("Ципрекс", "Ципрофлоксацин", "ЛП-000004"),
            ],
        ),
        (
            "фенозепам",
            "similar",
            [
                DrugMatch("Феназепам", "Бромдигидрохлорфенилбензодиазепин", "ЛП-000006"),
                DrugMatch("Нозепам", "Оксазепам", "ЛП-000007"),
            ],
        ),
        # БАД в реестре лекарств не значится.
        ("омега-3", "not_found", []),
        ("", "not_found", []),
        ("®", "not_found", []),
        (
            "Приостанин",
            "suspended",
            [DrugMatch("Приостанин", "Тестовое вещество один", "ЛП-000010")],
        ),
        ("Неясин", "unknown", [DrugMatch("Неясин", "Тестовое вещество два", "ЛП-000011")]),
        ("Старин", "inactive", [DrugMatch("Старин", "Тестовое вещество три", "П N000012/01")]),
        # Одно название, два удостоверения: истёкшее и действующее. Показываем действующее.
        ("Дуплин", "registered", [DrugMatch("Дуплин", "Тестовое вещество четыре", "ЛП-000014")]),
    ],
)
def test_lookup(registry: Registry, name: str, check: str, matches: list[DrugMatch]) -> None:
    result = registry.lookup(name)

    assert (result.name, result.check, list(result.matches)) == (name, check, matches)


def test_short_first_word_is_not_a_key(registry: Registry) -> None:
    # «Но» из «Но-шпа» совпало бы с любым коротким словом; ключ первого слова — от 4 букв.
    assert registry.lookup("но").check == "not_found"


def test_unknown_status_in_snapshot_is_an_error(tmp_path: Path) -> None:
    path = tmp_path / "grls.csv"
    path.write_text("reg_number,trade_name,inn,status\nЛП-1,Тест,Тест,maybe\n", encoding="utf-8")

    with pytest.raises(ValueError, match="строка 2: неизвестный статус maybe"):
        load_registry(path)


def test_repository_snapshot_has_aviandr() -> None:
    registry = load_registry(REPO_KNOWLEDGE / "sources" / "files" / "grls.csv")

    result = registry.lookup("Авиандр")

    assert result.check == "registered"
    assert result.matches[0].inn == "Маритупирдин"


@pytest.mark.parametrize(
    ("raw", "name"),
    [
        ("Авиандр 50 мг", "Авиандр"),
        ("авиандр 50мг утром", "авиандр"),
        ("таблетки авиандр", "авиандр"),
        ("Но-шпа", "Но-шпа"),
        ("Магне B6", "Магне"),
        ("таблетки", ""),
        ("витамины", ""),
        # Цифра в первом слове — часть названия БАДа: «нет в ГРЛС» врач должен увидеть.
        ("омега-3", "омега-3"),
        ("коэнзим Q10", "коэнзим"),
        ("", ""),
    ],
)
def test_drug_name_drops_dose_and_form(raw: str, name: str) -> None:
    assert drug_name(raw) == name


def test_blank_trade_name_does_not_break_index() -> None:
    registry = Registry([RegistryEntry("ЛП-1", "  ", "Тестовое вещество", "active")])

    assert registry.lookup("Тестовое вещество").check == "registered"


def _entry(number: str, trade: str, inn: str, status: str = "active") -> RegistryEntry:
    return RegistryEntry(number, trade, inn, status)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("entries", "name", "inn"),
    [
        # Одно название, несколько удостоверений с одним МНН — МНН известен.
        (
            [_entry("1", "Двоин", "Вещество"), _entry("2", "Двоин®", "Вещество")],
            "Двоин",
            "Вещество",
        ),
        # Статус регистрации не важен: для взаимодействий важен состав.
        (
            [_entry("1", "Двоин", "Вещество", "inactive"), _entry("2", "Двоин", "Вещество")],
            "Двоин",
            "Вещество",
        ),
        # Регистр МНН в снимке разный, вещество одно.
        ([_entry("1", "Двоин", "Вещество"), _entry("2", "Двоин", "ВЕЩЕСТВО")], "Двоин", "Вещество"),
        # Разные МНН под одним названием — выбирает врач.
        ([_entry("1", "Двоин", "Первое"), _entry("2", "Двоин", "Второе")], "Двоин", None),
        # Пустой МНН в части записей не мешает: состав известен по остальным.
        ([_entry("1", "Двоин", ""), _entry("2", "Двоин", "Вещество")], "Двоин", "Вещество"),
        # Пустой МНН — не МНН.
        ([_entry("1", "Двоин", "")], "Двоин", None),
        # Название и есть МНН.
        ([_entry("1", "Двоин", "Вещество")], "вещество", "Вещество"),
        # Похожее название — МНН выбирает врач.
        ([_entry("1", "Двоинн", "Вещество")], "Двоин", None),
        ([_entry("1", "Двоин", "Вещество")], "Другое", None),
    ],
)
def test_inn_is_known_when_all_entries_agree(
    entries: list[RegistryEntry], name: str, inn: str | None
) -> None:
    assert Registry(entries).lookup(name).inn == inn


@pytest.mark.parametrize(
    ("name", "inn"),
    [
        # Ингибиторы МАО и триптан под торговыми названиями: у каждого 2–3 удостоверения.
        ("Пиразидол", "Пирлиндол"),
        ("Азилект", "Разагилин"),
        ("Амигренин", "Суматриптан"),
        ("Аурорикс", "Моклобемид"),
        # У «Деприма» МНН в снимке не заполнен.
        ("Деприм", None),
    ],
)
def test_repository_snapshot_inn_of_trade_names(name: str, inn: str | None) -> None:
    registry = load_registry(REPO_KNOWLEDGE / "sources" / "files" / "grls.csv")

    assert registry.lookup(name).inn == inn


@pytest.mark.parametrize(
    ("entries", "name", "options"),
    [
        # Разные МНН у одного названия: каждый вариант один раз, все статусы, без обрезки.
        (
            [
                _entry("1", "Двоин", "Первое"),
                _entry("2", "Двоин", "Второе"),
                _entry("3", "Двоин", "ПЕРВОЕ"),
                _entry("4", "Двоин", "Третье", "inactive"),
                _entry("5", "Двоин", "Четвёртое"),
                _entry("6", "Двоин", ""),
            ],
            "Двоин",
            ("Первое", "Второе", "Третье", "Четвёртое"),
        ),
        # МНН один — выбирать нечего.
        ([_entry("1", "Двоин", "Вещество"), _entry("2", "Двоин", "Вещество")], "Двоин", ()),
        # Похожее название — МНН похожих названий.
        ([_entry("1", "Двоинн", "Вещество")], "Двоин", ("Вещество",)),
        ([_entry("1", "Двоин", "Вещество")], "Другое", ()),
    ],
)
def test_inn_options_list_every_inn_once(
    entries: list[RegistryEntry], name: str, options: tuple[str, ...]
) -> None:
    assert Registry(entries).lookup(name).inn_options == options


def test_repository_snapshot_offers_both_spellings_of_st_johns_wort() -> None:
    # Ревью #76: у «Зверобоя травы» два написания МНН, врач выбирает из обоих.
    registry = load_registry(REPO_KNOWLEDGE / "sources" / "files" / "grls.csv")

    assert set(registry.lookup("Зверобоя трава").inn_options) == {
        "Зверобоя трава",
        "Зверобоя продырявленного трава",
    }
