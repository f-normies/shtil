"""Раздел документа по ссылке `locator` из knowledge/ (спека #106, 3.1).

`locator` — свободный текст: «4.3; 4.2, «Пациенты с почечной недостаточностью»»,
«раздел 3.1.3: …», «прил. Г1, ключ: …». Номер раздела берётся из начала каждой части
через «;», остальное — пояснение для человека.

У ОХЛП номер раздела лежит в `section` (`4.3`). У КР `section` — ключ разметки (`doc_3`),
а номер подраздела есть только в уровнях `heading_path`: «3. Лечение > 3.1 … > 3.1.3 …».
"""

import re
from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from clin_compass.modules.knowledge import models
from clin_compass.modules.knowledge.schemas import Fragment, SourceSection
from clin_compass.modules.knowledge.search import load_fragments, to_schema

# Необязательное слово «раздел», «пункт» или «п.», затем номер вида 4, 4.3, 3.1.3.
_NUMBER = re.compile(r"\s*(?:(?:раздел|пункт|п\.)\s*)?(\d+(?:\.\d+)*)", re.IGNORECASE)
_SECTION_NUMBER = re.compile(r"\d+(?:\.\d+)*")
# Уровни заголовков в `heading_path`.
LEVEL_SEPARATOR = " > "


def parse_locator(locator: str) -> tuple[str, ...]:
    """Номера разделов из `locator` по порядку, без повторов.

    Часть без номера в начале («прил. Г1», «с. 39», номер РУ) раздела не даёт.
    """
    numbers: list[str] = []
    for part in locator.split(";"):
        match = _NUMBER.match(part)
        if match is not None and match.group(1) not in numbers:
            numbers.append(match.group(1))
    return tuple(numbers)


def _split_level(level: str) -> tuple[str, str]:
    """Номер уровня (первое слово без конечной точки) и заголовок после него."""
    words = level.split(maxsplit=1)
    if not words:
        return "", ""
    return words[0].rstrip("."), words[1] if len(words) > 1 else ""


def _levels(fragment: Fragment) -> list[tuple[str, str]]:
    return [_split_level(level) for level in fragment.heading_path.split(LEVEL_SEPARATOR)]


def _within(number: str, section: str) -> bool:
    """`number` — раздел `section` или его подраздел: 4.3 и 4.3.1 входят в 4.3, 4.30 — нет."""
    return number == section or number.startswith(f"{section}.")


def belongs(fragment: Fragment, section: str) -> bool:
    """Фрагмент относится к разделу: номер есть в `section` или в уровне `heading_path`."""
    numbers = [fragment.section, *(number for number, _ in _levels(fragment))]
    return any(_within(number, section) for number in numbers)


def _title(fragments: Sequence[Fragment], section: str) -> str:
    """Заголовок раздела из уровня `heading_path` с этим номером, иначе `section_title`."""
    for fragment in fragments:
        for number, title in _levels(fragment):
            if number == section:
                return title
        if fragment.section == section:
            return fragment.section_title
    return ""


def select_sections(fragments: Sequence[Fragment], numbers: Sequence[str]) -> list[SourceSection]:
    """Разделы с номерами `numbers` в их порядке; раздел без фрагментов не попадает в ответ.

    `fragments` — фрагменты одного документа в порядке документа, он сохраняется.
    """
    sections = []
    for number in numbers:
        matched = [fragment for fragment in fragments if belongs(fragment, number)]
        if matched:
            sections.append(
                SourceSection(number=number, title=_title(matched, number), fragments=matched)
            )
    return sections


def fragment_section(fragment: Fragment) -> SourceSection:
    """Раздел одного фрагмента: самый глубокий нумерованный уровень `heading_path`.

    Без нумерованного уровня — номер из `section`, если это номер (ОХЛП), иначе без номера.
    """
    numbered = [level for level in _levels(fragment) if _SECTION_NUMBER.fullmatch(level[0])]
    if numbered:
        number, title = numbered[-1]
        return SourceSection(number=number, title=title, fragments=[fragment])
    number_or_none = fragment.section if _SECTION_NUMBER.fullmatch(fragment.section) else None
    return SourceSection(number=number_or_none, title=fragment.section_title, fragments=[fragment])


async def read_sections(session: AsyncSession, source_id: str, locator: str) -> list[SourceSection]:
    """Разделы действующей версии документа по `locator`; нет документа или раздела — пусто.

    Документ небольшой (до ~200 фрагментов): фрагменты читаются целиком и отбираются здесь.
    """
    numbers = parse_locator(locator)
    if not numbers:
        return []
    rows = await session.execute(
        select(models.Fragment, models.Document)
        .join(models.Document, models.Document.id == models.Fragment.document_id)
        .where(models.Document.source_id == source_id, models.Document.status == "active")
        .order_by(models.Fragment.position)
    )
    return select_sections([to_schema(fragment, document) for fragment, document in rows], numbers)


async def read_fragment(
    session: AsyncSession, source_id: str, fragment_id: UUID
) -> SourceSection | None:
    """Раздел с одним фрагментом по id, в том числе из прежней версии документа.

    None — фрагмента нет или он из другого источника.
    """
    fragment = (await load_fragments(session, [fragment_id])).get(fragment_id)
    if fragment is None or fragment.document.source_id != source_id:
        return None
    return fragment_section(fragment)
