"""Фрагмент документа после нарезки: до эмбеддинга и записи в базу (спека #27, раздел 3)."""

from dataclasses import dataclass
from typing import Literal

FragmentKind = Literal["recommendation", "text", "table"]


@dataclass(frozen=True)
class ParsedFragment:
    """Фрагмент из разбора клинической рекомендации или инструкции.

    `section` — `id` раздела API рубрикатора (`doc_3`) или номер раздела ОХЛП (`4.3`).
    `heading_path` — заголовки от раздела до фрагмента через « > ». `text` — текст, с которым
    сверяется цитата. `uur` (A, B, C) и `udd` (1–5) заданы только у `recommendation`.
    """

    section: str
    section_title: str
    heading_path: str
    kind: FragmentKind
    text: str
    uur: str | None = None
    udd: int | None = None


class ParseError(ValueError):
    """Документ не разобран: формат не узнан или не прошёл контроль нарезки (спека #27, 3.2–3.5).

    Текст ошибки называет место в документе и не повторяет его содержимое целиком.
    """
