"""Части фрагмента для цитаты по номерам (спека #70, 6.1).

Модель указывает диапазон номеров частей, код вырезает из текста фрагмента участок
от начала первой части до конца последней. Цитата дословная и непрерывная по построению.

Часть — непустая строка текста: пункт списка, строка таблицы (с « | »), абзац. Абзац делится
на предложения: граница — знак конца предложения, пробел и заглавная буква, цифра или скобка.
Поэтому «мг/сут. в течение», «т. е.» и «0.5 мг» предложение не обрывают.
"""

import re
from collections.abc import Sequence

_LINE = re.compile(r"[^\n]+")
_SENTENCE_BREAK = re.compile(r"(?<=[.!?…])\s+(?=[А-ЯЁA-Z0-9«\"(\[])")
_TABLE_CELL = " | "

Span = tuple[int, int]


def _strip(text: str, start: int, end: int) -> Span | None:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return (start, end) if start < end else None


def units(text: str) -> list[Span]:
    """Границы частей фрагмента в исходном тексте: [начало, конец)."""
    spans: list[Span] = []
    for line in _LINE.finditer(text):
        start, end = line.span()
        if _TABLE_CELL in line.group():
            pieces = [(start, end)]
        else:
            pieces = []
            cursor = start
            for brk in _SENTENCE_BREAK.finditer(text, start, end):
                pieces.append((cursor, brk.start()))
                cursor = brk.end()
            pieces.append((cursor, end))
        for piece in pieces:
            span = _strip(text, *piece)
            if span is not None:
                spans.append(span)
    return spans


def quote_span(text: str, spans: Sequence[Span], first: int, last: int) -> str | None:
    """Участок текста от начала части `first` до конца части `last` (с 1); вне частей — None."""
    if first < 1 or last < first or last > len(spans):
        return None
    return text[spans[first - 1][0] : spans[last - 1][1]]


def numbered(text: str) -> str:
    """Текст фрагмента для промпта: каждая часть с новой строки с номером `(n)`."""
    return "\n".join(f"({n}) {text[s:e]}" for n, (s, e) in enumerate(units(text), start=1))
