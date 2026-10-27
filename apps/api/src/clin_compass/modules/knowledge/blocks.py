"""HTML → блоки и упаковка блоков во фрагменты (спека #27, 3.2, 3.3 и 3.5).

Блок — заголовок с уровнем, абзац, пункт списка или таблица. Разбор на `html.parser`
из стандартной библиотеки: разметка рубрикатора и таблицы olmOCR 2 простые, своя
зависимость для них не нужна. Сущности раскрываются, пробелы схлопываются,
`&nbsp;` становится пробелом.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Literal

from clin_compass.modules.knowledge.fragments import ParsedFragment, ParseError

# Фрагменты `text` и части таблиц — до 2000 символов (3.2).
TEXT_LIMIT = 2000
# Вход длиннее `-ub 8192` токенов сервер эмбеддингов отвергает с 500 (3.5).
MAX_FRAGMENT = 20_000

BlockKind = Literal["heading", "paragraph", "item", "table"]

_HEADINGS = {f"h{level}": level for level in range(1, 7)}
# Теги, на которых кончается текущий блок. Остальные теги (strong, em, sup, span…) — строчные.
_BREAKS = {
    "p", "div", "ul", "ol", "li", "table", "blockquote", "pre", "section", "article",
    "dl", "dt", "dd", "hr", *_HEADINGS,
}  # fmt: skip
_CELLS = {"td", "th"}
_ITALIC = {"em", "i"}


@dataclass(frozen=True)
class Block:
    """Блок HTML. У таблицы `rows` — строки ячеек, `header` — сколько первых строк шапка.

    `emphasis` — больше половины знаков блока набрано курсивом (`<em>`, `<i>`): в рубрикаторе
    так набран комментарий к пункту рекомендации.
    """

    kind: BlockKind
    text: str = ""
    level: int = 0
    rows: tuple[tuple[str, ...], ...] = ()
    header: int = 0
    emphasis: bool = False


def parse_html(html: str) -> list[Block]:
    """Разобрать HTML в блоки по порядку. Блоки без текста (картинки, пустые абзацы) не нужны."""
    parser = _Parser()
    parser.feed(html)
    parser.close()
    return parser.finish()


def pack_text(texts: Sequence[str], limit: int = TEXT_LIMIT) -> list[str]:
    """Упаковать тексты блоков в части до `limit` символов по границам блоков.

    Блоки внутри части разделяются переводом строки. Блок длиннее `limit` идёт отдельной частью:
    внутри блока не режем, предел всего фрагмента проверяет `check_lengths`.
    """
    parts: list[str] = []
    current = ""
    for text in texts:
        if current and len(current) + 1 + len(text) > limit:
            parts.append(current)
            current = ""
        current = f"{current}\n{text}" if current else text
    if current:
        parts.append(current)
    return parts


def table_parts(block: Block, limit: int = TEXT_LIMIT) -> list[str]:
    """Таблица → строки «ячейка | ячейка», части до `limit` символов по строкам.

    Строки шапки повторяются в начале каждой части: без них число в части не с чем связать.
    """
    lines = [" | ".join(row) for row in block.rows]
    head, body = lines[: block.header], lines[block.header :]
    if not body:
        return ["\n".join(head)] if head else []
    parts: list[str] = []
    current: list[str] = []
    for line in body:
        if current and len("\n".join([*head, *current, line])) > limit:
            parts.append("\n".join([*head, *current]))
            current = []
        current.append(line)
    parts.append("\n".join([*head, *current]))
    return parts


def check_lengths(fragments: Sequence[ParsedFragment]) -> None:
    """Проверить, что фрагменты не длиннее `MAX_FRAGMENT` символов (3.5); иначе `ParseError`."""
    for fragment in fragments:
        if len(fragment.text) > MAX_FRAGMENT:
            raise ParseError(
                f"фрагмент раздела {fragment.section} ({fragment.heading_path}) длиной "
                f"{len(fragment.text)} символов больше предела {MAX_FRAGMENT}"
            )


def _clean(text: str) -> str:
    """Схлопнуть пробелы: `str.split` делит и по неразрывному пробелу из `&nbsp;`."""
    return " ".join(text.split())


class _Parser(HTMLParser):
    """Собирает блоки. Таблица внутри ячейки таблицы читается как текст этой ячейки."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[Block] = []
        self._text: list[str] = []
        # Открытые пункты списка и заголовки: от них зависит вид блока при сбросе текста.
        self._open: list[str] = []
        self._italic = 0  # открытых <em> и <i>
        self._italic_chars = 0  # знаков без пробелов курсивом в текущем блоке
        self._table_depth = 0
        self._rows: list[tuple[list[str], bool, bool]] = []  # ячейки, все ли th, в thead ли
        self._row: list[str] | None = None
        self._row_th = True
        self._cell: list[str] | None = None
        self._in_thead = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self._table_depth += 1
            if self._table_depth == 1:
                self._flush()
                self._rows = []
                return
        if self._table_depth:
            self._table_start(tag)
            return
        if tag in _ITALIC:
            self._italic += 1
        elif tag == "br":
            self._text.append(" ")
        elif tag in _BREAKS:
            self._flush()
            if tag == "li" or tag in _HEADINGS:
                self._open.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if self._table_depth:
            if tag == "table":
                self._table_depth -= 1
                if not self._table_depth:
                    self._table_end()
                    return
            self._table_close(tag)
            return
        if tag in _ITALIC:
            self._italic = max(0, self._italic - 1)
        elif tag in _BREAKS:
            self._flush()
            if tag in self._open:
                while self._open.pop() != tag:
                    pass

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)
        elif not self._table_depth:
            self._text.append(data)
            if self._italic:
                self._italic_chars += len("".join(data.split()))

    def finish(self) -> list[Block]:
        """Сбросить хвост текста и вернуть блоки."""
        if self._table_depth:
            self._table_depth = 0
            self._table_end()
        self._flush()
        return self.blocks

    def _flush(self) -> None:
        text = _clean("".join(self._text))
        emphasis = 2 * self._italic_chars > len(text.replace(" ", ""))
        self._text, self._italic_chars = [], 0
        if not text:
            return
        top = self._open[-1] if self._open else ""
        if top in _HEADINGS:
            self.blocks.append(Block("heading", text, level=_HEADINGS[top]))
        elif top == "li":
            self.blocks.append(Block("item", text, emphasis=emphasis))
        else:
            self.blocks.append(Block("paragraph", text, emphasis=emphasis))

    def _table_start(self, tag: str) -> None:
        if self._table_depth > 1 or tag not in {"tr", "thead", *_CELLS}:
            if self._cell is not None:
                self._cell.append(" ")
            return
        if tag == "thead":
            self._in_thead = True
        elif tag == "tr":
            self._close_row()
            self._row, self._row_th = [], True
        else:
            self._close_cell()
            if self._row is None:
                self._row, self._row_th = [], True
            self._cell = []
            self._row_th = self._row_th and tag == "th"

    def _table_close(self, tag: str) -> None:
        if self._table_depth > 1:
            return
        if tag in _CELLS:
            self._close_cell()
        elif tag == "tr":
            self._close_row()
        elif tag == "thead":
            self._close_row()
            self._in_thead = False
        elif self._cell is not None:
            self._cell.append(" ")

    def _close_cell(self) -> None:
        if self._cell is not None and self._row is not None:
            self._row.append(_clean("".join(self._cell)))
        self._cell = None

    def _close_row(self) -> None:
        self._close_cell()
        if self._row is not None:
            self._rows.append((self._row, self._row_th, self._in_thead))
        self._row = None

    def _table_end(self) -> None:
        self._close_row()
        self._in_thead = False
        rows: list[tuple[str, ...]] = []
        flags: list[tuple[bool, bool]] = []
        for cells, all_th, in_thead in self._rows:
            while cells and not cells[-1]:
                cells.pop()
            if cells:
                rows.append(tuple(cells))
                flags.append((all_th, in_thead))
        self._rows = []
        if rows:
            self.blocks.append(Block("table", rows=tuple(rows), header=_header_rows(flags)))


def _header_rows(flags: Sequence[tuple[bool, bool]]) -> int:
    """Сколько первых строк — шапка таблицы.

    Строки в `<thead>` — шапка. Без `<thead>` шапкой считается первая строка из `<th>`, если
    других таких строк нет: у olmOCR 2 строки из `<th>` посреди таблицы — подзаголовки групп.
    """
    in_thead = 0
    while in_thead < len(flags) and flags[in_thead][1]:
        in_thead += 1
    if in_thead:
        return in_thead
    th_rows = [all_th for all_th, _ in flags]
    return 1 if th_rows[0] and len(flags) > 1 and not any(th_rows[1:]) else 0
