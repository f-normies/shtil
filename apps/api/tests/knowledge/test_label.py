"""Нарезка ОХЛП по страницам от olmOCR 2 (спека #27, 3.3).

Текст синтетический, в формате olmOCR 2: заголовки — обычные строки с номером,
таблицы — HTML. Препарат «Синтетин» выдуман.
"""

from dataclasses import dataclass

import pytest

from clin_compass.core.ai.textlayer import reconcile_pages
from clin_compass.modules.knowledge.fragments import FragmentKind, ParsedFragment, ParseError
from clin_compass.modules.knowledge.label import parse_label

PAGE_1 = """ОБЩАЯ ХАРАКТЕРИСТИКА ЛЕКАРСТВЕННОГО ПРЕПАРАТА

▼ Препарат подлежит дополнительному мониторингу.

1. НАИМЕНОВАНИЕ ЛЕКАРСТВЕННОГО ПРЕПАРАТА
СИНТЕТИН, 10 мг, таблетки.

2. КАЧЕСТВЕННЫЙ И КОЛИЧЕСТВЕННЫЙ СОСТАВ
Действующее вещество: синтетин.

3. ЛЕКАРСТВЕННАЯ ФОРМА
Таблетки.

4. КЛИНИЧЕСКИЕ ДАННЫЕ
Текст под разделом 4.
4.1. Показания к применению
Препарат показан взрослым для лечения:
• синтетического расстройства.

4.2. Режим дозирования и способ применения
Внутрь по 1 таблетке 2 раза в сутки у пациентов с жалобой на тревогу,
"""

PAGE_2 = """изменением фона настроения и раздражительностью.

4.3. Противопоказания
1. Гиперчувствительность к синтетину.
2. Почечная недостаточность.
4.2. Режим дозирования — см. выше.
5. Беременность.

## 4.4. Нежелательные реакции
<table>
  <tr>
    <th>Нарушения нервной системы</th>
    <th></th>
  </tr>
  <tr>
    <td>Часто</td>
    <td>головная боль</td>
  </tr>
</table>

5. ФАРМАКОЛОГИЧЕСКИЕ СВОЙСТВА
5.1. Фармакодинамические свойства
Механизм действия не изучен.
"""

CLINICAL = "4. КЛИНИЧЕСКИЕ ДАННЫЕ"
DOSING = "4.2. Режим дозирования и способ применения"


def _fragment(
    section: str, title: str, path: str, text: str, kind: FragmentKind = "text"
) -> ParsedFragment:
    return ParsedFragment(section, title, path, kind, text)


def test_sections_tables_and_page_join() -> None:
    assert parse_label([PAGE_1, PAGE_2]) == [
        _fragment(
            "0",
            "ОБЩАЯ ХАРАКТЕРИСТИКА ЛЕКАРСТВЕННОГО ПРЕПАРАТА",
            "ОБЩАЯ ХАРАКТЕРИСТИКА ЛЕКАРСТВЕННОГО ПРЕПАРАТА",
            "▼ Препарат подлежит дополнительному мониторингу.",
        ),
        _fragment(
            "1",
            "НАИМЕНОВАНИЕ ЛЕКАРСТВЕННОГО ПРЕПАРАТА",
            "1. НАИМЕНОВАНИЕ ЛЕКАРСТВЕННОГО ПРЕПАРАТА",
            "СИНТЕТИН, 10 мг, таблетки.",
        ),
        _fragment(
            "2",
            "КАЧЕСТВЕННЫЙ И КОЛИЧЕСТВЕННЫЙ СОСТАВ",
            "2. КАЧЕСТВЕННЫЙ И КОЛИЧЕСТВЕННЫЙ СОСТАВ",
            "Действующее вещество: синтетин.",
        ),
        _fragment("3", "ЛЕКАРСТВЕННАЯ ФОРМА", "3. ЛЕКАРСТВЕННАЯ ФОРМА", "Таблетки."),
        _fragment("4", "КЛИНИЧЕСКИЕ ДАННЫЕ", CLINICAL, "Текст под разделом 4."),
        _fragment(
            "4.1",
            "Показания к применению",
            f"{CLINICAL} > 4.1. Показания к применению",
            "Препарат показан взрослым для лечения:\n• синтетического расстройства.",
        ),
        _fragment(
            "4.2",
            "Режим дозирования и способ применения",
            f"{CLINICAL} > {DOSING}",
            "Внутрь по 1 таблетке 2 раза в сутки у пациентов с жалобой на тревогу,"
            " изменением фона настроения и раздражительностью.",
        ),
        _fragment(
            "4.3",
            "Противопоказания",
            f"{CLINICAL} > 4.3. Противопоказания",
            "1. Гиперчувствительность к синтетину.\n2. Почечная недостаточность.\n"
            "4.2. Режим дозирования — см. выше.\n5. Беременность.",
        ),
        _fragment(
            "4.4",
            "Нежелательные реакции",
            f"{CLINICAL} > 4.4. Нежелательные реакции",
            "Нарушения нервной системы\nЧасто | головная боль",
            kind="table",
        ),
        _fragment(
            "5.1",
            "Фармакодинамические свойства",
            "5. ФАРМАКОЛОГИЧЕСКИЕ СВОЙСТВА > 5.1. Фармакодинамические свойства",
            "Механизм действия не изучен.",
        ),
    ]


@pytest.mark.parametrize(
    ("end", "start", "joined"),
    [
        pytest.param("в сутки,", "утром и вечером.", "в сутки, утром и вечером.", id="фраза"),
        pytest.param("частота", "(<1/10000).", "частота (<1/10000).", id="скобка"),
        pytest.param(
            "под кривой", "«концентрация–время».", "под кривой «концентрация–время».", id="кавычка"
        ),
        pytest.param("в сутки.", "Курс 4 недели.", "в сутки.\nКурс 4 недели.", id="новый абзац"),
        pytest.param("в сутки", "Курс 4 недели.", "в сутки\nКурс 4 недели.", id="заглавная буква"),
    ],
)
def test_page_join(end: str, start: str, joined: str) -> None:
    pages = [f"4.1. Показания к применению\nТревога.\n{DOSING}\nПо 1 таблетке {end}\n", start]
    pages.append("4.3. Противопоказания\nНет.")

    dosing = [f for f in parse_label(pages) if f.section == "4.2"]

    assert [f.text for f in dosing] == [f"По 1 таблетке {joined}"]


def test_long_section_is_split_by_paragraphs() -> None:
    body = "\n\n".join(["а" * 900, "б" * 900, "в" * 900])
    page = f"4.1. Показания\nТревога.\n4.2. Режим\nВнутрь.\n4.3. Противопоказания\n{body}"

    contra = [f.text for f in parse_label([page]) if f.section == "4.3"]

    assert contra == ["а" * 900 + "\n" + "б" * 900, "в" * 900]


def test_subheading_stays_with_its_paragraph() -> None:
    body = "\n".join(["а" * 1000 + ".", "Ингибиторы изофермента", "б" * 1000 + "."])
    page = f"4.1. Показания\nТревога.\n4.2. Режим\nВнутрь.\n4.3. Противопоказания\n{body}"

    contra = [f.text for f in parse_label([page]) if f.section == "4.3"]

    assert contra == ["а" * 1000 + ".", "Ингибиторы изофермента\n" + "б" * 1000 + "."]


@pytest.mark.parametrize(
    "pages",
    [
        pytest.param([], id="нет страниц"),
        pytest.param(["4.1. Показания\nТревога.\n4.2. Режим\nВнутрь."], id="нет 4.3"),
        pytest.param(
            ["ИНСТРУКЦИЯ ПО МЕДИЦИНСКОМУ ПРИМЕНЕНИЮ\nПоказания к применению\nТревога."],
            id="не формат ЕАЭС",
        ),
    ],
)
def test_missing_core_sections_is_parse_error(pages: list[str]) -> None:
    with pytest.raises(ParseError, match=r"4\.1, 4\.2 и 4\.3"):
        parse_label(pages)


@pytest.mark.parametrize(
    ("extra", "missing"),
    [
        pytest.param("4.5. Взаимодействие\nНет.", "4.4", id="пропущен 4.4"),
        pytest.param("5. СВОЙСТВА\n5.1. Фармакодинамика\nА.\n5.3. Доклиника\nБ.", "5.2", id="5.2"),
        pytest.param("6. ФАРМАЦЕВТИКА\n6.2. Несовместимость\nНет.", "6.1", id="6.1"),
    ],
)
def test_gap_in_subsection_numbering_is_parse_error(extra: str, missing: str) -> None:
    page = f"4.1. Показания\nТревога.\n4.2. Режим\nВнутрь.\n4.3. Противопоказания\nНет.\n{extra}"

    with pytest.raises(ParseError, match=rf"нет раздела {missing}\b"):
        parse_label([page])


def test_fragment_over_length_limit_is_parse_error() -> None:
    page = f"4.1. Показания\n{'т' * 20_001}\n4.2. Режим\nВнутрь.\n4.3. Противопоказания\nНет."

    with pytest.raises(ParseError, match="20000"):
        parse_label([page])


@dataclass(frozen=True)
class _Page:
    number: int
    text: str
    layer_text: str


def _dosing_page(dosing: str) -> str:
    return (
        f"4.1. Показания\nТревога.\n4.2. Режим дозирования\n{dosing}\n4.3. Противопоказания\nНет."
    )


@pytest.mark.parametrize(
    ("model", "layer", "expected"),
    [
        pytest.param(
            "<table><tr><td>АЛТ &lt;ULN</td><td>нет коррекции</td></tr></table>",
            "АЛТ <ULN нет коррекции",
            ("table", "АЛТ <ULN | нет коррекции"),
            id="«<ULN» в ячейке",
        ),
        pytest.param(
            "<table><tr><td>СКФ <60</td><td>АЛТ &gt;3</td><td>A&B</td></tr></table>",
            "СКФ <60 АЛТ >3 A&B",
            ("table", "СКФ <60 | АЛТ >3 | A&B"),
            id="«<60», «>» и «&» в ячейках",
        ),
        pytest.param(
            "СКФ <60 мл/мин; ЧСС >100; R&D; АЛТ <ULN.",
            "СКФ <60 мл/мин; ЧСС >100; R&D; АЛТ <ULN.",
            ("text", "СКФ <60 мл/мин; ЧСС >100; R&D; АЛТ <ULN."),
            id="в обычном тексте",
        ),
    ],
)
def test_layer_text_reaches_fragments(model: str, layer: str, expected: tuple[str, str]) -> None:
    page = _Page(1, _dosing_page(model), _dosing_page(layer))
    [reconciled], mismatches = reconcile_pages([page])

    dosing = [(f.kind, f.text) for f in parse_label([reconciled.text]) if f.section == "4.2"]

    assert mismatches == []
    assert dosing == [expected]


def test_text_lost_by_html_parsing_is_parse_error() -> None:
    # «<ULN» без экранирования HTML-разбор читает как тег, и условие пропадает.
    page = _dosing_page("<table><tr><td>АЛТ <ULN</td><td>нет коррекции</td></tr></table>")

    with pytest.raises(ParseError, match="ULN"):
        parse_label([page])
