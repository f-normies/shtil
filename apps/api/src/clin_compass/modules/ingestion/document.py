"""Текст документа из страниц docparse и поиск цитат в нём (спека #30, 3.3 и 5).

Источник страницы:
- свелась со слоем PDF — сведённый текст (`layer_markup`);
- не свелась, но слой полный — слой PDF (`layer`);
- слоя нет или в нём меньше половины слов модели (колонтитул, штамп на скане) — текст модели
  (`ocr`).

Таблицы в тексте модели — HTML olmOCR или markdown. Они становятся строками «ячейка | ячейка»:
так строку таблицы можно процитировать, а модель видит, какие ячейки стоят рядом. LaTeX
и HTML-сущности снимаются. Этим же текстом пользуется `eval/extraction`.
"""

import html
import re
from collections.abc import Sequence
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Literal

from clin_compass_knowledge.checks import quote_in_text

from clin_compass.core.ai.docparse import ParsedPage
from clin_compass.core.ai.latex import strip_latex
from clin_compass.core.ai.textlayer import reconcile_pages
from clin_compass.modules.ingestion.schemas import PageSource

# Извлечение идёт одним вызовом модели: ответ должен уложиться в 6144 токена и 180 с.
MAX_TEXT_LENGTH = 15_000
PAGE_SEPARATOR = "\n\n"
CELL_SEPARATOR = " | "
# Слой короче половины текста модели — не текст страницы, а колонтитул или штамп скана.
_LAYER_SHARE = 0.5
_TABLE = re.compile(r"<table\b.*?</table>", re.IGNORECASE | re.DOTALL)
_TAG = re.compile(r"<[^<>]+>")
_MARKDOWN_RULE = re.compile(r"^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?$")
_WORD = re.compile(r"\w+")
# Где модель склеивает цитату из нескольких мест: многоточие или граница предложений.
_QUOTE_GAP = re.compile(r"\s*(?:\.\.\.|…)\s*|(?<=[.!?])\s+")


@dataclass(frozen=True)
class PageSpan:
    """Страница в тексте документа: источник и границы в символах."""

    page: int
    source: PageSource
    start: int
    end: int


@dataclass(frozen=True)
class DocumentText:
    text: str
    pages: tuple[PageSpan, ...]

    @property
    def ocr(self) -> bool:
        """Хотя бы одна страница распознана с изображения."""
        return any(p.source == "ocr" for p in self.pages)


def document_text(pages: Sequence[ParsedPage]) -> DocumentText:
    """Текст документа: источник каждой страницы, таблицы строками, страницы через пустую строку."""
    reconciled, mismatches = reconcile_pages(pages)
    failed = {m.number: m for m in mismatches}
    parts: list[str] = []
    spans: list[PageSpan] = []
    position = 0
    for page, joined in zip(pages, reconciled, strict=True):
        mismatch = failed.get(page.number)
        source: PageSource
        if mismatch is None:
            text, source = markup_text(joined.text), "layer_markup"
        elif mismatch.no_layer or _thin_layer(page):
            text, source = markup_text(strip_latex(page.text)), "ocr"
        else:
            text, source = _plain_layer(page.layer_text), "layer"
        text = text.strip()
        if parts:
            position += len(PAGE_SEPARATOR)
        spans.append(PageSpan(page.number, source, position, position + len(text)))
        parts.append(text)
        position += len(text)
    return DocumentText(PAGE_SEPARATOR.join(parts), tuple(spans))


def markup_text(text: str) -> str:
    """Разметка модели как текст: таблицы строками, HTML-сущности раскрыты."""
    return html.unescape(flatten_tables(text))


def flatten_tables(text: str) -> str:
    """HTML- и markdown-таблицы — строки «ячейка | ячейка»; строка-разделитель markdown уходит."""
    text = _TABLE.sub(lambda m: _html_rows(m.group()), text)
    lines: list[str] = []
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("|") and _MARKDOWN_RULE.fullmatch(stripped):
            continue
        if stripped.startswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            line = CELL_SEPARATOR.join(cells)
        lines.append(line)
    return "\n".join(lines)


def quote_found(quote: str, text: str) -> bool:
    """Цитата есть в тексте (`quote_in_text`); разделитель ячеек « | » считается пробелом.

    Строку таблицы модель может процитировать с разделителем или без него. Модель склеивает
    цитату из нескольких мест через «…» или из предложений разных абзацев, хотя промпт это
    запрещает. Тогда в тексте дословно должна найтись каждая часть: выдуманная часть цитату
    отбрасывает.
    """
    whole, where = quote.replace("|", " "), text.replace("|", " ")
    if quote_in_text(whole, where):
        return True
    parts = [p for p in _QUOTE_GAP.split(whole) if _WORD.search(p)]
    return len(parts) > 1 and all(quote_in_text(p, where) for p in parts)


def quote_page(quote: str, text: str, pages: Sequence[PageSpan]) -> int | None:
    """Первая страница, на которой есть цитата. None — цитата переходит через границу страниц."""
    return next((p.page for p in pages if quote_found(quote, text[p.start : p.end])), None)


def text_problem(text: str) -> Literal["empty_text", "text_too_long"] | None:
    """Почему текст нельзя отдать на извлечение: пустой или длиннее `MAX_TEXT_LENGTH`."""
    if not text.strip():
        return "empty_text"
    if len(text) > MAX_TEXT_LENGTH:
        return "text_too_long"
    return None


def _thin_layer(page: ParsedPage) -> bool:
    model = len(_WORD.findall(_TAG.sub(" ", strip_latex(page.text))))
    return len(_WORD.findall(page.layer_text)) < _LAYER_SHARE * model


def _plain_layer(layer_text: str) -> str:
    """Слой pdfium как текст: переводы строк `\\n`, знак переноса 0x02 склеивает слово."""
    return layer_text.replace("\r\n", "\n").replace("\r", "\n").replace("\x02", "")


class _Rows(HTMLParser):
    """Строки HTML-таблицы: ячейки `td` и `th`, `br` внутри ячейки — пробел."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self.rows.append([])
        elif tag in {"td", "th"}:
            if not self.rows:
                self.rows.append([])
            self._cell = []
        elif tag == "br" and self._cell is not None:
            self._cell.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._cell is not None:
            self.rows[-1].append(" ".join("".join(self._cell).split()))
            self._cell = None

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)


def _html_rows(table: str) -> str:
    parser = _Rows()
    parser.feed(table)
    parser.close()
    # Сущности в ячейках парсер уже раскрыл.
    return "\n".join(CELL_SEPARATOR.join(r) for r in parser.rows if r)
