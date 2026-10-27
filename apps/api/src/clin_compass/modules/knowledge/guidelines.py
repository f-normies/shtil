"""Разбор клинической рекомендации из API рубрикатора Минздрава (спека #27, 3.2).

На входе — JSON ответа `GetClinrec2` целиком. Совпадение `id` с версией из манифеста
проверяет индексатор. Здесь — разделы `obj.sections`: HTML раздела → блоки → фрагменты.

Пункт рекомендации закрывает строка уровней (УУР и УДД). Текст пункта начинается с ближайшего
перед ней блока со словом «рекомендуется». В рубрикаторе встречаются и «рекомендовано»,
«рекомендована», «рекомендуются», и опечатка «рекомедуется»: выражение принимает все.
Если между двумя строками уровней такого блока нет, это вариант общего пункта («- или …»):
у каждого варианта своя строка уровней. Тогда фрагмент — вступление общего пункта и сам вариант.
Комментарий к пункту — «Комментарии…» и курсивные абзацы после него.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from clin_compass.modules.knowledge.blocks import (
    Block,
    check_lengths,
    pack_text,
    parse_html,
    table_parts,
)
from clin_compass.modules.knowledge.fragments import FragmentKind, ParsedFragment, ParseError

# Документ целиком, титульный лист, ключевые слова, сокращения, литература,
# рабочая группа, методология и шкалы не индексируются.
SKIPPED_SECTIONS = frozenset(
    {
        "doc_whole",
        "doc_title",
        "doc_key_words",
        "doc_abbreviation",
        "doc_bible",
        "doc_a1",
        "doc_a2",
        "doc_g",
    }
)

_DASH = r"[-‐‑‒–—−]"
_GRADE = re.compile(
    rf"уровень\s+убедительности\s+рекомендаци[йи]\s*{_DASH}?\s*([ABCАВС])\s*"
    rf"\(\s*уровень\s+достоверности\s+доказательств\s*{_DASH}?\s*([1-5])\s*\)\s*\.?",
    re.IGNORECASE,
)
_RECOMMENDS = re.compile(r"\bрекомен?д(?:уется|уются|ован[оаы]?)\b", re.IGNORECASE)
_COMMENT = re.compile(r"комментари[йи]\b", re.IGNORECASE)
_LATIN = str.maketrans("АВСabcавс", "ABCABCABC")


@dataclass(frozen=True)
class _Item:
    """Пункт: блоки `first`…`end - 1`, строка уровней `grade`, вступление общего пункта `lead`."""

    first: int
    grade: int
    end: int
    lead: int | None
    uur: str
    udd: int


def parse_guideline(data: Mapping[str, Any]) -> list[ParsedFragment]:
    """Нарезать клиническую рекомендацию на фрагменты в порядке документа.

    Raises:
        ParseError: нет `obj.sections`; строка уровней не стоит отдельным абзацем, и число
            пунктов не равно числу строк уровней; фрагмент длиннее предела (3.5).
    """
    fragments: list[ParsedFragment] = []
    for section_id, title, html in _sections(data):
        if section_id in SKIPPED_SECTIONS:
            continue
        blocks = parse_html(html)
        found = _section_fragments(section_id, title, blocks)
        grades = sum(len(_GRADE.findall(text)) for block in blocks for text in _texts(block))
        items = sum(f.kind == "recommendation" for f in found)
        if items != grades:
            raise ParseError(
                f"раздел {section_id}: строк уровней {grades}, а пунктов рекомендаций {items}. "
                "Строка уровней должна стоять отдельным абзацем после пункта"
            )
        fragments.extend(found)
    check_lengths(fragments)
    return fragments


def _sections(data: Mapping[str, Any]) -> list[tuple[str, str, str]]:
    """Разделы ответа API: `id`, заголовок и HTML (пустой раздел — пустая строка)."""
    obj = data.get("obj")
    sections = obj.get("sections") if isinstance(obj, Mapping) else None
    if not isinstance(sections, list):
        raise ParseError("в ответе API рубрикатора нет списка obj.sections")
    result = []
    for number, section in enumerate(sections):
        if not isinstance(section, Mapping):
            raise ParseError(f"obj.sections[{number}] — не объект")
        section_id, title, html = section.get("id"), section.get("title"), section.get("content")
        if not isinstance(section_id, str) or not isinstance(title, str):
            raise ParseError(f"obj.sections[{number}]: нет id или title")
        if html is not None and not isinstance(html, str):
            raise ParseError(f"obj.sections[{number}] ({section_id}): content — не строка")
        result.append((section_id, title, html or ""))
    return result


def _texts(block: Block) -> list[str]:
    """Тексты блока: у таблицы — каждая ячейка."""
    if block.kind == "table":
        return [cell for row in block.rows for cell in row]
    return [block.text]


def _is_text(block: Block) -> bool:
    return block.kind in ("paragraph", "item")


def _grade(block: Block) -> tuple[str, int] | None:
    """УУР и УДД, если блок — строка уровней целиком. Кириллические А, В, С → латинские."""
    match = _GRADE.fullmatch(block.text) if _is_text(block) else None
    if match is None:
        return None
    return match.group(1).translate(_LATIN), int(match.group(2))


def _items(blocks: Sequence[Block]) -> list[_Item]:
    """Найти пункты: начало, строку уровней, вступление варианта и конец комментария.

    Блоки комментария началом пункта не бывают, даже со словом «рекомендуется»: комментарий
    к варианту «- Йота» часто советует что-то врачу, а следующий вариант «- или Каппа» слова
    не содержит.
    """
    in_comment = _comment_blocks(blocks)
    items: list[_Item] = []
    lead: int | None = None  # начало последнего пункта со своим словом «рекомендуется»
    floor = 0  # первый блок после предыдущего пункта с комментарием или после заголовка
    for index, block in enumerate(blocks):
        if block.kind == "heading":
            floor, lead = index + 1, None
            continue
        grade = _grade(block)
        if grade is None:
            continue
        starts = [
            i
            for i in range(floor, index)
            if _is_text(blocks[i]) and not in_comment[i] and _RECOMMENDS.search(blocks[i].text)
        ]
        if starts:
            lead = first = starts[-1]
            item_lead = None
        else:
            # Вариант общего пункта: последний блок перед строкой уровней.
            body = index - 1
            usable = body >= floor and _is_text(blocks[body]) and not in_comment[body]
            first, item_lead = (body if usable else index), lead
        end = _comment_end(blocks, index + 1)
        items.append(_Item(first, index, end, item_lead, *grade))
        floor = end
    return items


def _comment_blocks(blocks: Sequence[Block]) -> list[bool]:
    """Отметить блоки комментариев: «Комментарии…» и его продолжение."""
    marks = [False] * len(blocks)
    index = 0
    while index < len(blocks):
        end = _comment_end(blocks, index)
        for i in range(index, end):
            marks[i] = True
        index = max(end, index + 1)
    return marks


def _comment_end(blocks: Sequence[Block], start: int) -> int:
    """Конец комментария, который начинается в `start`; `start` — комментария там нет.

    Комментарий начинается со слов «Комментарии» или «Комментарий». Продолжение набрано
    курсивом, как в рубрикаторе. Обычный абзац, таблица, заголовок, строка уровней или пункт
    списка со словом «рекомендуется» комментарий заканчивают: иначе обычный текст или пункт
    без уровней попал бы во фрагмент с чужими УУР и УДД.
    """
    if start >= len(blocks) or not _is_text(blocks[start]):
        return start
    if not _COMMENT.match(blocks[start].text):
        return start
    end = start + 1
    while end < len(blocks):
        block = blocks[end]
        recommends = block.kind == "item" and _RECOMMENDS.search(block.text)
        if not _is_text(block) or not block.emphasis or recommends or _grade(block):
            break
        end += 1
    return end


def _lines(block: Block) -> list[str]:
    """Текст блока для фрагмента: таблица — строками «ячейка | ячейка»."""
    if block.kind == "table":
        return [" | ".join(row) for row in block.rows]
    return [block.text]


def _section_fragments(
    section_id: str, title: str, blocks: Sequence[Block]
) -> list[ParsedFragment]:
    """Фрагменты раздела по порядку: пункты, текст между ними и таблицы."""
    items = {item.first: item for item in _items(blocks)}
    fragments: list[ParsedFragment] = []
    headings: list[tuple[int, str]] = []
    run: list[str] = []

    def path() -> str:
        return " > ".join([title, *(text for _, text in headings)])

    def add(kind: FragmentKind, text: str, uur: str | None = None, udd: int | None = None) -> None:
        fragments.append(ParsedFragment(section_id, title, path(), kind, text, uur, udd))

    def flush() -> None:
        for part in pack_text(run):
            add("text", part)
        run.clear()

    index = 0
    while index < len(blocks):
        block = blocks[index]
        item = items.get(index)
        if item is not None:
            flush()
            lead = [blocks[item.lead].text] if item.lead is not None else []
            body = [line for b in blocks[item.first : item.end] for line in _lines(b)]
            add("recommendation", "\n".join(lead + body), item.uur, item.udd)
            index = item.end
            continue
        if block.kind == "heading":
            flush()
            while headings and headings[-1][0] >= block.level:
                headings.pop()
            headings.append((block.level, block.text))
        elif block.kind == "table":
            flush()
            for part in table_parts(block):
                add("table", part)
        else:
            run.append(block.text)
        index += 1
    flush()
    return fragments
