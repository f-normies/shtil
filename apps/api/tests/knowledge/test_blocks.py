"""HTML → блоки и упаковка блоков во фрагменты (спека #27, 3.2 и 3.5). Данные синтетические."""

import pytest

from clin_compass.modules.knowledge.blocks import (
    MAX_FRAGMENT,
    Block,
    check_lengths,
    pack_text,
    parse_html,
    table_parts,
)
from clin_compass.modules.knowledge.fragments import ParsedFragment, ParseError


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        pytest.param(
            "<h2>3.1 Первый этап</h2><h3>3.1.1 Лёгкая степень</h3>",
            [
                Block("heading", "3.1 Первый этап", level=2),
                Block("heading", "3.1.1 Лёгкая степень", level=3),
            ],
            id="заголовки с уровнем",
        ),
        pytest.param(
            "<p><strong>Пациентам</strong>&nbsp;<em>рекомендуется</em>   приём\n по&nbsp;20&nbsp;мг"
            " &laquo;утром&raquo; &amp; вечером</p>",
            [Block("paragraph", "Пациентам рекомендуется приём по 20 мг «утром» & вечером")],
            id="сущности и пробелы",
        ),
        pytest.param(
            "<ul><li><p>первый</p></li><li>второй<ul><li><p>вложенный</p></li></ul></li></ul>",
            [Block("item", "первый"), Block("item", "второй"), Block("item", "вложенный")],
            id="пункты списка",
        ),
        pytest.param(
            '<p><img src="data:image/png;base64,AAAA"><br class="ProseMirror-trailingBreak"></p>'
            "<p>мг/м<sup>2</sup> и H<sub>2</sub>O<br>с новой строки</p>",
            [Block("paragraph", "мг/м2 и H2O с новой строки")],
            id="картинка без текста пропускается",
        ),
        pytest.param(
            "<p>до</p>текст без тега<p>после</p>",
            [
                Block("paragraph", "до"),
                Block("paragraph", "текст без тега"),
                Block("paragraph", "после"),
            ],
            id="текст вне блока",
        ),
    ],
)
def test_parse_html_blocks(html: str, expected: list[Block]) -> None:
    assert parse_html(html) == expected


@pytest.mark.parametrize(
    ("html", "rows", "header"),
    [
        pytest.param(
            "<table><thead><tr><th><p>Препарат</p></th><th><p>Доза, мг</p></th></tr></thead>"
            '<tbody><tr><td colspan="2"><p><strong>Группа</strong></p></td></tr>'
            "<tr><td><p>Альфа</p></td><td><p>20–50</p></td></tr></tbody></table>",
            (("Препарат", "Доза, мг"), ("Группа",), ("Альфа", "20–50")),
            1,
            id="шапка в thead",
        ),
        pytest.param(
            "<table><tr><th>Препарат</th><th>Доза</th></tr><tr><td>Альфа</td><td>20</td></tr></table>",
            (("Препарат", "Доза"), ("Альфа", "20")),
            1,
            id="первая строка из th",
        ),
        pytest.param(
            "<table>\n  <tr>\n    <th>Нарушения сна</th>\n    <th></th>\n  </tr>\n"
            "  <tr>\n    <td>Часто</td>\n    <td>бессонница</td>\n  </tr>\n"
            "  <tr>\n    <th>Нарушения кожи</th>\n    <th></th>\n  </tr>\n"
            "  <tr><td></td><td></td></tr>\n"
            "  <tr>\n    <td>Редко</td>\n    <td>сыпь</td>\n  </tr>\n</table>",
            (("Нарушения сна",), ("Часто", "бессонница"), ("Нарушения кожи",), ("Редко", "сыпь")),
            0,
            id="строки th внутри таблицы — группы, а не шапка",
        ),
    ],
)
def test_parse_html_table(html: str, rows: tuple[tuple[str, ...], ...], header: int) -> None:
    assert parse_html(f"<p>до</p>{html}<p>после</p>") == [
        Block("paragraph", "до"),
        Block("table", rows=rows, header=header),
        Block("paragraph", "после"),
    ]


@pytest.mark.parametrize(
    ("html", "emphasis"),
    [
        pytest.param("<p><em>Курсивный абзац комментария.</em></p>", True, id="курсив"),
        pytest.param(
            "<p><strong>Комментарий:</strong> <em>длинный курсивный текст комментария.</em></p>",
            True,
            id="курсив больше половины",
        ),
        pytest.param("<p><i>Курсив</i> через <em>i</em> и <em>em</em></p>", True, id="i и em"),
        pytest.param(
            "<p>Обычный абзац с <em>термином</em> внутри текста.</p>", False, id="обычный"
        ),
        pytest.param("<ul><li><p><em>Курсивный пункт.</em></p></li></ul>", True, id="пункт"),
    ],
)
def test_emphasis_marks_mostly_italic_blocks(html: str, emphasis: bool) -> None:
    [block] = parse_html(html)

    assert block.emphasis is emphasis


@pytest.mark.parametrize(
    ("texts", "limit", "expected"),
    [
        pytest.param(["a" * 10, "b" * 10], 30, ["a" * 10 + "\n" + "b" * 10], id="всё в одну часть"),
        pytest.param(
            ["a" * 10, "b" * 10, "c" * 10],
            21,
            ["a" * 10 + "\n" + "b" * 10, "c" * 10],
            id="ровно лимит",
        ),
        pytest.param(
            ["a" * 10, "b" * 50, "c" * 10], 30, ["a" * 10, "b" * 50, "c" * 10], id="длинный блок"
        ),
        pytest.param([], 30, [], id="пусто"),
    ],
)
def test_pack_text_splits_at_block_boundaries(
    texts: list[str], limit: int, expected: list[str]
) -> None:
    assert pack_text(texts, limit) == expected


def test_table_parts_repeat_header_and_fit_limit() -> None:
    rows = (("Препарат", "Доза"), *((f"Препарат {i}", f"{i}0 мг") for i in range(1, 7)))
    table = Block("table", rows=rows, header=1)

    parts = table_parts(table, limit=60)

    assert parts == [
        "Препарат | Доза\nПрепарат 1 | 10 мг\nПрепарат 2 | 20 мг",
        "Препарат | Доза\nПрепарат 3 | 30 мг\nПрепарат 4 | 40 мг",
        "Препарат | Доза\nПрепарат 5 | 50 мг\nПрепарат 6 | 60 мг",
    ]
    assert all(len(part) <= 60 for part in parts)


def test_table_without_header_is_one_part_under_limit() -> None:
    table = Block("table", rows=(("Часто", "бессонница"), ("Редко", "сыпь")))

    assert table_parts(table) == ["Часто | бессонница\nРедко | сыпь"]


def _fragment(text: str) -> ParsedFragment:
    return ParsedFragment("doc_3", "3. Лечение", "3. Лечение", "text", text)


def test_fragment_at_length_limit_passes() -> None:
    check_lengths([_fragment("а" * MAX_FRAGMENT)])


def test_fragment_over_length_limit_is_parse_error() -> None:
    with pytest.raises(ParseError, match=r"doc_3.*20001"):
        check_lengths([_fragment("а"), _fragment("а" * (MAX_FRAGMENT + 1))])
