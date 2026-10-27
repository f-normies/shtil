"""Факторы поиска для подбора (спека #70, 5.3): чистые функции без сети и базы.

- Сокращения: словарь из раздела «Список сокращений» КР и файла исправлений, раскрытие
  в запросе целым словом с учётом регистра.
- Выбор документа: начала слов вопроса сравниваются с терминами источника.
- Части длинных фрагментов: по абзацам, длинный абзац — по предложениям.
- Текст документа для эмбеддинга, полнотекстового поиска и ранжировщика.
"""

import html
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

_TAG = re.compile(r"<[^>]+>")
_ROW = re.compile(r"<tr\b.*?</tr>", re.IGNORECASE | re.DOTALL)
_CELL = re.compile(r"<t[dh]\b.*?</t[dh]>", re.IGNORECASE | re.DOTALL)
_BREAK = re.compile(r"</p>|<br\s*/?>|</li>|</div>", re.IGNORECASE)
_DASH = re.compile(r"\s+[–—-]\s+")
_LEAD_DASH = re.compile(r"^[–—-]\s*")
_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")
_WORD = re.compile(r"\w+")


def _text(fragment: str) -> str:
    return " ".join(html.unescape(_TAG.sub(" ", fragment)).split())


def parse_abbreviations(section_html: str) -> dict[str, str]:
    """Сокращение → полная форма из HTML раздела «Список сокращений».

    Понимает таблицу из двух ячеек («ГТР» | «– генерализованное…») и строки «АД – антидепрессант».
    """
    result: dict[str, str] = {}
    rows = _ROW.findall(section_html)
    if rows:
        for row in rows:
            cells = [_text(cell) for cell in _CELL.findall(row)]
            if len(cells) >= 2 and cells[0] and cells[1]:
                result[cells[0]] = _LEAD_DASH.sub("", cells[1]).strip()
        return result
    for line in _BREAK.split(section_html):
        parts = _DASH.split(_text(line), maxsplit=1)
        if len(parts) == 2 and parts[0] and parts[1]:
            result[parts[0]] = parts[1]
    return result


@dataclass(frozen=True)
class Abbreviations:
    """Словарь сокращений и раскрытие запроса."""

    full: Mapping[str, str]
    _patterns: tuple[tuple[re.Pattern[str], str], ...] = field(repr=False)

    @classmethod
    def build(
        cls, sources: Iterable[Mapping[str, str]], fixes: Mapping[str, str]
    ) -> "Abbreviations":
        """Объединяет словари источников; исправления заменяют записи источников."""
        full: dict[str, str] = {}
        for source in sources:
            full.update(source)
        full.update(fixes)
        # Длинные сокращения первыми: «СИОЗСН» не должно раскрыться как «СИОЗС».
        patterns = tuple(
            (re.compile(rf"(?<![\w-]){re.escape(short)}(?![\w-])"), expansion)
            for short, expansion in sorted(full.items(), key=lambda item: -len(item[0]))
        )
        return cls(full=full, _patterns=patterns)

    def expansions(self, question: str) -> list[str]:
        """Полные формы сокращений вопроса в порядке их появления."""
        found: list[tuple[int, str]] = []
        taken: list[tuple[int, int]] = []
        for pattern, expansion in self._patterns:
            for match in pattern.finditer(question):
                span = match.span()
                if any(start < span[1] and span[0] < end for start, end in taken):
                    continue
                taken.append(span)
                found.append((span[0], expansion))
        ordered: list[str] = []
        for _, expansion in sorted(found):
            if expansion not in ordered:
                ordered.append(expansion)
        return ordered

    def for_embedding(self, question: str) -> str:
        """Вопрос с полными формами в скобках; без сокращений — как есть."""
        found = self.expansions(question)
        return f"{question} ({'; '.join(found)})" if found else question

    def for_full_text(self, question: str) -> str:
        """Вопрос с полными формами через пробел: леммы соединятся через «ИЛИ»."""
        found = self.expansions(question)
        return " ".join([question, *found]) if found else question


def chosen_sources(question: str, terms: Mapping[str, Sequence[str]]) -> set[str]:
    """Источники, к которым относится вопрос: слово вопроса начинается с термина источника."""
    words = _WORD.findall(question.lower().replace("ё", "е"))
    return {
        source
        for source, prefixes in terms.items()
        if any(word.startswith(prefix) for word in words for prefix in prefixes)
    }


def _sentences_into_parts(paragraph: str, limit: int) -> list[str]:
    parts: list[str] = []
    current = ""
    for sentence in _SENTENCE_END.split(paragraph):
        candidate = f"{current} {sentence}".strip()
        if current and len(candidate) > limit:
            parts.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        parts.append(current)
    return parts


def split_parts(text: str, *, threshold: int, limit: int) -> list[str]:
    """Части фрагмента для векторного поиска.

    Фрагмент не длиннее `threshold` — одна часть. Длиннее — абзацы, соседние короткие абзацы
    объединяются до `limit`; абзац длиннее `limit` режется по предложениям.
    """
    if len(text) <= threshold:
        return [text]
    parts: list[str] = []
    current = ""
    for paragraph in (p.strip() for p in text.split("\n")):
        if not paragraph:
            continue
        if len(paragraph) > limit:
            if current:
                parts.append(current)
                current = ""
            parts.extend(_sentences_into_parts(paragraph, limit))
            continue
        candidate = f"{current}\n{paragraph}" if current else paragraph
        if len(candidate) > limit:
            parts.append(current)
            current = paragraph
        else:
            current = candidate
    if current:
        parts.append(current)
    return parts


def document_text(title: str, heading_path: str, text: str, context: str | None) -> str:
    """Текст фрагмента для эмбеддинга и ранжировщика: как в боевой индексации, плюс контекст."""
    head = f"{title}. {heading_path}"
    return f"{head}\n{context}\n{text}" if context else f"{head}\n{text}"
