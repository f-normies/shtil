"""Сведение страниц ОХЛП из реестра ЕАЭС с текстовым слоем PDF (#105). Тексты синтетические.

Переносы слов и слитые ячейки в слое, LaTeX и ошибки модели в таблицах, страница из слоя.
"""

import pytest

from clin_compass.core.ai.textlayer import MAX_UNMATCHED, reconcile_pages
from tests.knowledge.test_textlayer import LAYER, MODEL, PARAGRAPH, TABLE, _one, _Page


def test_line_break_marker_is_joined_or_hyphen_as_on_page() -> None:
    # pdfium отдаёт и перенос слова, и дефис в конце строки одним знаком 0x02 без перевода строки.
    # Как напечатано, видно только по странице: модель пишет «корпусом», но «COVID-19».
    model = MODEL + "Таблетки, покрытые корпусом; COVID-19 и темно-красно-коричневого цвета.\n"
    layer = (
        LAYER + "Таблетки, покрытые кор\x02пусом; COVID\x0219 и темно\x02красно-корич\x02невого"
        " цвета.\n"
    )

    page, mismatches = _one(model, layer)

    assert mismatches == []
    assert "покрытые корпусом; COVID-19 и темно-красно-коричневого цвета." in page.text


def test_several_line_breaks_in_one_word() -> None:
    # В узкой ячейке слово переносится дважды; дефис сложного слова тоже бывает в конце строки.
    model = MODEL + "Реакции гиперчувствительности, сердечно-сосудистые нарушения.\n"
    layer = LAYER + (
        "Реакции ги\x02перчувстви\x02тельности, сердечно\x02сосу\x02дистые нарушения.\n"
    )

    page, mismatches = _one(model, layer)

    assert mismatches == []
    assert "Реакции гиперчувствительности, сердечно-сосудистые нарушения." in page.text


def test_line_break_in_misread_word() -> None:
    # Модель прочла слово иначе, подсказки на странице нет. Цельное слово есть на другой
    # странице слоя; без подсказок перенос между строчными буквами — перенос слова.
    pages = [
        _Page(1, MODEL, LAYER + "Нечасто косоглазие.\n"),
        _Page(
            2,
            "Редко конъюнктивит, кровоточение и ВИЧ-инфекцие.\n",
            "Редко ко\x02соглазие, крово\x02течение и ВИЧ\x02инфекция.\n",
        ),
    ]

    [_, page], mismatches = reconcile_pages(pages)

    assert mismatches == []
    assert page.text == "Редко косоглазие, кровотечение и ВИЧ-инфекция.\n"


@pytest.mark.parametrize(
    ("model", "layer", "expected"),
    [
        pytest.param(
            "<table><tr><td>нарушения</td><td>Состояние</td></tr></table>",
            "нарушенияСостояние",
            "<table><tr><td>нарушения</td><td>Состояние</td></tr></table>",
            id="ячейки слиты в слое",
        ),
        pytest.param(
            "<table><tr><td>гиперчувствительности</td><td>Ангионевротический</td></tr></table>",
            "ги\x02перчувстви\x02тельностиАнгионевро\x02тический",
            "<table><tr><td>гиперчувствительности</td><td>Ангионевротический</td></tr></table>",
            id="слиты и перенесены",
        ),
        pytest.param(
            "<table><tr><td>Нарушения</td><td>Состояние</td><td>Печень</td><td>Почкм</td></tr>"
            "</table>",
            "НарушенияСостояниеПеченьПочки",
            "<table><tr><td>Нарушения</td><td>Состояние</td><td>Печень</td><td>Почки</td></tr>"
            "</table>",
            id="четыре ячейки, одну модель прочла иначе",
        ),
        pytest.param(
            "Кислотность pH желудка.", "Кислотность pH желудка.", "Кислотность pH желудка.", id="pH"
        ),
        pytest.param(
            "Хранить при температуре не выше 25 °C.",
            "Хранить при температуре не выше 25 ºС.",
            "Хранить при температуре не выше 25 ºС.",
            id="знак градуса",
        ),
    ],
)
def test_glued_table_cells_are_split_as_on_page(model: str, layer: str, expected: str) -> None:
    # pdfium не ставит пробел между соседними ячейками таблицы; граница видна по тексту модели.
    page, mismatches = _one(model, layer)

    assert mismatches == []
    assert page.text == expected


def test_latex_from_model_is_markup() -> None:
    # olmOCR 2 пишет формулы и знаки в LaTeX; в слое PDF — обычные символы.
    model = MODEL + (
        r"Пища снижает \( C_{\text{max}} \) на 25–30%; масса \( \geq 30 \) кг; "
        r"\[ CL_{cr}(\text{мл/мин}) = \left[ \frac{1,23 \times [140 - \text{возраст}]}"
        r"{\text{креатинин} \left( \frac{\text{ммоль}}{\text{л}} \right)} \right] \]"
        "\n"
    )
    layer = LAYER + (
        "Пища снижает Cmax на 25–30%; масса ≥ 30 кг;\nС𝐿𝑐𝑟(мл/мин) = [\n1,23 × [140 − возраст]"
        "\nкреатинин (\nммоль\nл\n)\n]\n"
    )

    page, mismatches = _one(model, layer)

    assert mismatches == []
    assert "Пища снижает Cmax на 25–30%; масса ≥ 30 кг;" in page.text
    # Математический курсив слоя — те же буквы: «𝐿𝑐𝑟» в тексте фрагмента мешал бы поиску.
    assert "СLcr(мл/мин) = [" in page.text
    assert "\\" not in page.text


# Таблица нежелательных реакций: термин и частота в каждой строке, частоты чередуются.
TERMS = [
    *("диплопия", "косоглазие", "некролиз", "гипорефлексия", "дисграфия", "осциллопсия"),
    *("нистагм", "миоклонус", "атаксия", "дизартрия", "тремор", "амнезия"),
]
FREQUENCIES = ("часто", "нечасто", "редко")
# Настоящая страница ОХЛП — 400–900 токенов; таблица на ней — малая часть.
FILLER = " ".join(f"Предложение {n} о синтетине без изменений." for n in range(50))


def _reactions(
    terms: list[str],
    *,
    reversed_cells: bool = False,
    long: bool = True,
    layer_terms: list[str] | None = None,
) -> tuple[str, str]:
    rows = [(t, FREQUENCIES[n % 3]) for n, t in enumerate(TERMS)]
    pairs = zip(terms, (f for _, f in rows), strict=True)
    cells = [(b, a) if reversed_cells else (a, b) for a, b in pairs]
    table = "<table>" + "".join(f"<tr><td>{a}</td><td>{b}</td></tr>" for a, b in cells)
    lines = "\n".join(f"{t} {f}" for t, (_, f) in zip(layer_terms or TERMS, rows, strict=True))
    filler = f"{FILLER}\n" if long else ""
    return MODEL + filler + table + "</table>\n", LAYER + filler + lines + "\n"


def test_many_misread_words_in_table_are_not_mismatch() -> None:
    # olmOCR 2 путает редкие термины в плотной таблице: «косоглазие» → «конъюнктивит». Замена
    # слова на месте не сдвигает ячейки, слово берётся из слоя.
    misread = {"диплопия": "дилюция", "косоглазие": "конъюнктивит", "некролиз": "некроз"}
    misread |= {"гипорефлексия": "гипопрефлексия", "дисграфия": "дистрафия"}
    misread |= {"осциллопсия": "осциллюсия"}
    model, layer = _reactions([misread.get(t, t) for t in TERMS])

    page, mismatches = _one(model, layer)

    assert mismatches == []
    assert not page.layer_only
    assert "<tr><td>косоглазие</td><td>нечасто</td></tr>" in page.text
    assert "конъюнктивит" not in page.text


def test_many_replaced_words_are_mismatch() -> None:
    # Испорченная кодировка шрифта в слое: каждое слово таблицы «заменено». Таким словам верить
    # нельзя, ни в разметке модели, ни без неё.
    garbled = [t.encode("cp1251").decode("latin-1") for t in TERMS]
    model, layer = _reactions(list(TERMS), long=False, layer_terms=garbled)

    _, [mismatch] = _one(model, layer)

    assert mismatch.unmatched > MAX_UNMATCHED


def test_words_missing_in_model_cells_take_page_from_layer() -> None:
    # Модель оставила пустыми ячейки терминов. Разметка модели посадила бы каждый термин
    # в ячейку частоты соседней строки; слой знает порядок.
    model = (
        MODEL
        + f"{FILLER}\n<table>"
        + "".join(f"<tr><td></td><td>{FREQUENCIES[n % 3]}</td></tr>" for n in range(len(TERMS)))
    )
    _, layer = _reactions(list(TERMS))

    page, mismatches = _one(model + "</table>\n", layer)

    assert mismatches == []
    assert page.layer_only
    assert "диплопия часто\nкосоглазие нечасто" in page.text


def _grouped_cells(*, long: bool = True) -> str:
    # Так olmOCR 2 записал таблицу реакций Ципралекса: частоты трёх строк в одной ячейке,
    # реакции — в соседней. Слой читает таблицу по строкам.
    rows = "".join(
        "<tr><td>{}</td><td>{}</td></tr>".format(
            "<br>".join(FREQUENCIES), "<br>".join(TERMS[n : n + 3])
        )
        for n in range(0, len(TERMS), 3)
    )
    return MODEL + (f"{FILLER}\n" if long else "") + f"<table>{rows}</table>\n"


@pytest.mark.parametrize(
    "model",
    [
        pytest.param(_reactions(list(TERMS), reversed_cells=True)[0], id="ячейки переставлены"),
        pytest.param(_grouped_cells(), id="частоты трёх строк в одной ячейке"),
    ],
)
def test_reordered_table_cells_take_page_from_layer(model: str) -> None:
    # Каждое слово сводится, но порядок мелкими кусками разошёлся: в разметке модели частота
    # ушла бы к соседнему термину. Слова и порядок слоя верны, страница берётся из слоя.
    _, layer = _reactions(list(TERMS))

    page, mismatches = _one(model, layer)

    assert mismatches == []
    assert page.layer_only
    assert page.text == layer.removeprefix("3\n").replace("<1/1000", "&lt;1/1000").rstrip("\n")
    assert "диплопия часто\nкосоглазие нечасто" in page.text


@pytest.mark.parametrize(
    ("model", "layer"),
    [
        pytest.param(
            _reactions(list(TERMS), reversed_cells=True, long=False)[0],
            _reactions(list(TERMS), long=False)[1],
            id="ячейки переставлены на короткой странице",
        ),
        pytest.param(
            _grouped_cells(long=False),
            _reactions(list(TERMS), long=False)[1],
            id="частоты в одной ячейке на короткой странице",
        ),
        pytest.param(
            MODEL.replace(TABLE, "").replace(PARAGRAPH, f"{TABLE}\n\n{PARAGRAPH}"),
            LAYER,
            id="таблица перед абзацем",
        ),
    ],
)
def test_many_moved_words_are_mismatch(model: str, layer: str) -> None:
    # Переставлена большая доля страницы: слой тоже может читать её неверно (колонки, ячейки
    # в несколько строк), кто прав — не видно.
    _, [mismatch] = _one(model, layer)

    assert mismatch.unmatched > MAX_UNMATCHED


def test_line_break_between_digits_is_hyphen() -> None:
    # Склейка по чтению модели сделала бы из диапазона «5-10 мг» дозу «510 мг».
    model = MODEL + "Доза 510 мг, по 12 таблетки.\n"
    layer = LAYER + "Доза 5\x0210 мг, по 1\x022 таблетки.\n"

    page, mismatches = _one(model, layer)

    assert mismatches == []
    assert "Доза 5-10 мг, по 1-2 таблетки." in page.text


def test_chain_of_parts_counts_only_whole() -> None:
    # «не» и «до» есть на странице, но «статочность» — не слово: цепочка «не-до-статочность»
    # не подходит, а цельное слово документа — подходит.
    pages = [
        _Page(1, MODEL, LAYER + "Почечная недостаточность.\n"),
        _Page(
            2, "Не до конца ясна недостатчность.\n", "Не до конца ясна не\x02до\x02статочность.\n"
        ),
    ]

    [_, page], mismatches = reconcile_pages(pages)

    assert mismatches == []
    assert page.text == "Не до конца ясна недостаточность.\n"
