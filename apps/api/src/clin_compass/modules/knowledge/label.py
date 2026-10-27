"""Нарезка ОХЛП по тексту страниц от olmOCR 2 (спека #27, 3.3).

Страницы склеиваются: фраза может продолжаться на следующей странице. Граница раздела —
строка с номером «N.» или «N.N.»: olmOCR 2 пишет заголовки обычными строками, иногда с `#`.
Номера в тексте раздела («1. Гиперчувствительность…») заголовками не считаются. Заголовок
принимается, только если номера идут по возрастанию, как в структуре ОХЛП ЕАЭС. Раздел верхнего
уровня, кроме того, написан заглавными буквами и не кончается знаком препинания: так строка
нумерованного списка «5. Беременность.» не становится разделом 5. Пропущенный заголовок
не ломает следующие: «5.1.» после «4.9.» начинает раздел 5.
Таблицы olmOCR 2 отдаёт в HTML, они режутся как в рекомендациях.
"""

import html
import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass

from clin_compass.core.ai.textlayer import words
from clin_compass.modules.knowledge.blocks import (
    Block,
    check_lengths,
    pack_text,
    parse_html,
    table_parts,
)
from clin_compass.modules.knowledge.fragments import FragmentKind, ParsedFragment, ParseError

# Без этих разделов нарезка не узнала формат ОХЛП ЕАЭС.
REQUIRED_SECTIONS = ("4.1", "4.2", "4.3")

_HEADING = re.compile(r"(?:#{1,6}\s*)?(?:\*\*)?(\d{1,2})\.(?:(\d{1,2})\.)?\s+(.+?)(?:\*\*)?")
_MARKUP = re.compile(r"^(?:#{1,6}\s*)?(?:\*\*)?|(?:\*\*)?$")
# После такого знака на конце страницы следующая страница начинает новый абзац.
_PARAGRAPH_END = (".", "!", "?", ":", ">")
# Подзаголовок внутри раздела: строка не длиннее этого и без знака препинания на конце.
_SUBHEADING_MAX = 120
_LINE_END = (".", "!", "?", ":", ";", ",")
# Строка страницы, которая начинается со строчной буквы или с этих знаков, продолжает фразу.
_PHRASE_START = ("(", "«", '"')


@dataclass(frozen=True)
class _Unit:
    """Строка текста с раскрытыми HTML-сущностями или HTML таблицы целиком."""

    text: str
    table: bool = False


@dataclass(frozen=True)
class _Section:
    """Раздел: номер («4.3»), подпись, путь заголовков, строка заголовка и блоки текста."""

    number: str
    title: str
    path: str
    heading: str
    blocks: tuple[Block, ...]


def parse_label(pages: Sequence[str]) -> list[ParsedFragment]:
    """Нарезать ОХЛП на фрагменты: текст разделов до 2000 символов по абзацам, таблицы — частями.

    Текст до раздела 1 идёт в раздел «0» с подписью из первой строки документа.

    Raises:
        ParseError: не найдены разделы 4.1, 4.2 и 4.3; в нумерации подразделов пропуск;
            нарезка потеряла или изменила текст
            страниц; фрагмент длиннее предела (3.5).
    """
    sections = _sections(_units(_lines(pages)))
    missing = [number for number in REQUIRED_SECTIONS if number not in {s.number for s in sections}]
    if missing:
        raise ParseError(
            "нарезка не узнала формат ОХЛП ЕАЭС: нужны разделы 4.1, 4.2 и 4.3, "
            f"не найдены: {', '.join(missing)}"
        )
    _check_numbering(sections)
    _check_text(pages, sections)
    fragments = [fragment for section in sections for fragment in _fragments(section)]
    check_lengths(fragments)
    return fragments


def _check_numbering(sections: Sequence[_Section]) -> None:
    """Подразделы идут без пропусков от N.1 до последнего найденного (в ОХЛП ЕАЭС 4.1–4.9).

    Пропуск значит, что заголовок не узнан, и его текст ушёл в предыдущий подраздел.
    """
    found: dict[int, set[int]] = {}
    for section in sections:
        top, _, sub = section.number.partition(".")
        if sub:
            found.setdefault(int(top), set()).add(int(sub))
    for top_number, subs in sorted(found.items()):
        gap = next((m for m in range(1, max(subs) + 1) if m not in subs), None)
        if gap is not None:
            raise ParseError(
                f"нарезка не узнала формат ОХЛП ЕАЭС: нет раздела {top_number}.{gap}, "
                f"хотя есть {top_number}.{max(subs)}"
            )


def _check_text(pages: Sequence[str], sections: Sequence[_Section]) -> None:
    """Слова и знаки страниц по порядку совпадают с тем, что уходит во фрагменты.

    Так ловится текст, который потерял HTML-разбор таблицы: например, «<ULN» без экранирования
    читается как тег. Шапка таблицы здесь учитывается один раз, хотя повторяется в частях.
    """
    expected = words("\n".join(pages), markup=True)
    actual = words("\n".join(_stream(sections)), markup=False)
    if expected == actual:
        return
    index = next(
        (i for i, pair in enumerate(zip(expected, actual, strict=False)) if pair[0] != pair[1]),
        min(len(expected), len(actual)),
    )
    start = max(0, index - 3)
    raise ParseError(
        "нарезка потеряла или изменила текст страниц: в тексте "
        f"«{' '.join(expected[start : index + 5])}», во фрагментах "
        f"«{' '.join(actual[start : index + 5])}»"
    )


def _stream(sections: Sequence[_Section]) -> Iterator[str]:
    """Текст разделов по порядку: заголовок, абзацы, строки таблиц."""
    for section in sections:
        yield section.heading
        for block in section.blocks:
            if block.kind == "table":
                yield "\n".join(" | ".join(row) for row in block.rows)
            else:
                yield block.text


def _lines(pages: Sequence[str]) -> list[str]:
    """Непустые строки всех страниц. Фраза, оборванная концом страницы, склеивается пробелом."""
    lines: list[str] = []
    for page in pages:
        page_lines = [line.strip() for line in page.splitlines() if line.strip()]
        if lines and page_lines and _continues(lines[-1], page_lines[0]):
            lines[-1] = f"{lines[-1]} {page_lines.pop(0)}"
        lines.extend(page_lines)
    return lines


def _continues(last: str, first: str) -> bool:
    """Продолжает ли первая строка страницы фразу с конца предыдущей."""
    starts_phrase = first[0].islower() or first.startswith(_PHRASE_START)
    return starts_phrase and not last.endswith(_PARAGRAPH_END)


def _units(lines: Sequence[str]) -> list[_Unit]:
    """Строки текста; HTML таблицы от `<table` до `</table>` — одна единица.

    Сведённая страница — HTML-разметка: «<» и «&» слоя в ней экранированы. В строке текста
    сущности раскрываются сразу, в таблице их раскрывает HTML-разбор.
    """
    units: list[_Unit] = []
    table: list[str] | None = None
    for line in lines:
        if table is None and line.startswith("<table"):
            table = []
        if table is None:
            units.append(_Unit(html.unescape(line)))
            continue
        table.append(line)
        if "</table>" in line:
            units.append(_Unit("\n".join(table), table=True))
            table = None
    if table is not None:
        units.append(_Unit("\n".join(table), table=True))
    return units


def _heading(unit: str, top: int, sub: int) -> tuple[int, int, str] | None:
    """Номер раздела, подраздела (0 у раздела верхнего уровня) и подпись — если это заголовок."""
    match = _HEADING.fullmatch(unit)
    if match is None:
        return None
    major, title = int(match.group(1)), match.group(3).strip()
    if not title[0].isupper() or title.endswith((";", ",", ":")):
        return None
    if match.group(2) is None:
        is_top = major > top and title == title.upper() and not title.endswith(".")
        return (major, 0, title) if is_top else None
    minor = int(match.group(2))
    if major > top or (major == top and minor > sub):
        return major, minor, title
    return None


def _sections(units: Sequence[_Unit]) -> list[_Section]:
    """Разбить строки на разделы. Строки до первого заголовка — раздел «0»."""
    heads: list[tuple[str, str, str, int]] = []  # номер, подпись, путь, индекс заголовка
    top, sub, top_line = 0, 0, ""
    for index, unit in enumerate(units):
        found = None if unit.table else _heading(unit.text, top, sub)
        if found is None:
            continue
        major, minor, title = found
        line = _MARKUP.sub("", unit.text).strip()
        if minor == 0:
            top, sub, top_line = major, 0, line
            heads.append((str(major), title, line, index))
            continue
        if major != top:
            top, top_line = major, ""
        sub = minor
        path = f"{top_line} > {line}" if top_line else line
        heads.append((f"{major}.{minor}", title, path, index))
    sections = []
    first_head = heads[0][3] if heads else len(units)
    if first_head > 0:
        preamble = units[:first_head]
        title = "" if preamble[0].table else preamble[0].text
        body = preamble if preamble[0].table else preamble[1:]
        sections.append(_Section("0", title, title, title, _blocks(body)))
    for number, (section, title, path, start) in enumerate(heads):
        end = heads[number + 1][3] if number + 1 < len(heads) else len(units)
        heading = units[start].text
        sections.append(_Section(section, title, path, heading, _blocks(units[start + 1 : end])))
    return sections


def _blocks(units: Sequence[_Unit]) -> tuple[Block, ...]:
    """Строки текста — абзацы, HTML таблицы — блоки HTML-разбора."""
    blocks: list[Block] = []
    for unit in units:
        if unit.table:
            blocks.extend(parse_html(unit.text))
        else:
            blocks.append(Block("paragraph", unit.text))
    return tuple(blocks)


def _fragments(section: _Section) -> list[ParsedFragment]:
    """Фрагменты раздела: абзацы до 2000 символов, таблицы частями с шапкой."""
    fragments: list[ParsedFragment] = []
    run: list[str] = []

    def add(kind: FragmentKind, text: str) -> None:
        fragments.append(ParsedFragment(section.number, section.title, section.path, kind, text))

    def flush() -> None:
        for part in pack_text(_glue_subheadings(run)):
            add("text", part)
        run.clear()

    for block in section.blocks:
        if block.kind != "table":
            run.append(block.text)
            continue
        flush()
        for part in table_parts(block):
            add("table", part)
    flush()
    return fragments


def _glue_subheadings(lines: Sequence[str]) -> list[str]:
    """Приклеить подзаголовок к следующей строке, чтобы упаковка не оставила его в конце части.

    Подзаголовок olmOCR 2 пишет обычной строкой: короткой и без знака препинания на конце.
    """
    glued: list[str] = []
    for line in lines:
        if glued and _is_subheading(glued[-1]):
            glued[-1] = f"{glued[-1]}\n{line}"
        else:
            glued.append(line)
    return glued


def _is_subheading(text: str) -> bool:
    last = text.rsplit("\n", 1)[-1]
    return len(last) <= _SUBHEADING_MAX and not last.endswith(_LINE_END)
