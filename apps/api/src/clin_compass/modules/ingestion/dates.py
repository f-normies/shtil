"""Даты из ответа модели сверяются с текстом документа (спека #30, раздел 5).

Модель отдаёт даты в ISO. Код оставляет дату, только если она написана в тексте: дата
документа — с годом где угодно в тексте, дата отмены — в цитате препарата, год можно
опустить. Отмена «10 дней назад» вычисляется от даты документа, если интервал есть в цитате.
"""

import re
from datetime import date, timedelta

_MONTHS = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)  # fmt: skip
_DOTTED = re.compile(r"(?<!\d)(\d{1,2})\.(\d{1,2})(?:\.(\d{4}|\d{2}))?(?!\d)")
_ISO = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")
_WORDS = re.compile(rf"(?<!\d)(\d{{1,2}})\s+({'|'.join(_MONTHS)})\s+(\d{{4}})(?!\d)")
# «10 дней назад», «2 недели назад», «за 3 суток до приёма»: число и единица.
_INTERVAL = re.compile(r"(?<!\d)(\d{1,3})\s*(дн|ден|сут|недел)", re.IGNORECASE)
_UNIT_DAYS = {"дн": 1, "ден": 1, "сут": 1, "недел": 7}


def parse_iso(value: str | None) -> date | None:
    """Дата ГГГГ-ММ-ДД из ответа модели; другая запись — None."""
    if value is None or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value.strip()):
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def date_in_text(day: date, text: str) -> bool:
    """Дата с годом есть в тексте: 14.10.2026, 14.10.26, 2026-10-14 или «14 октября 2026»."""
    for m in _DOTTED.finditer(text):
        if m.group(3) and _same(day, m.group(1), m.group(2), m.group(3)):
            return True
    if any(date(*map(int, m.groups())) == day for m in _ISO.finditer(text) if _valid_iso(m)):
        return True
    return any(
        int(m.group(1)) == day.day
        and _MONTHS.index(m.group(2)) + 1 == day.month
        and int(m.group(3)) == day.year
        for m in _WORDS.finditer(text.lower())
    )


def date_in_quote(day: date, quote: str) -> bool:
    """Дата есть в цитате как дд.мм или дд.мм.гггг (дд.мм.гг); без года — день и месяц."""
    return any(
        _same(day, m.group(1), m.group(2), m.group(3)) for m in _DOTTED.finditer(quote)
    ) or date_in_text(day, quote)


def days_before(document_date: date | None, days: int | None, quote: str) -> date | None:
    """Дата отмены за `days` дней до документа, если такой интервал написан в цитате."""
    if document_date is None or days is None or days < 0:
        return None
    for m in _INTERVAL.finditer(quote):
        if int(m.group(1)) * _UNIT_DAYS[m.group(2).lower()] == days:
            return document_date - timedelta(days=days)
    return None


def _same(day: date, dd: str, mm: str, yy: str | None) -> bool:
    if int(dd) != day.day or int(mm) != day.month:
        return False
    if yy is None:
        return True
    year = int(yy)
    return year == day.year if len(yy) == 4 else year == day.year % 100


def _valid_iso(match: re.Match[str]) -> bool:
    try:
        date(*map(int, match.groups()))
    except ValueError:
        return False
    return True
