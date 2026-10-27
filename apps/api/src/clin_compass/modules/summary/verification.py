"""Проверка утверждений сводки кодом (спека #38, 3.3).

Утверждение модели остаётся, только если:

- у него есть номера фактов и все они из списка;
- каждое число из текста есть в тексте указанных фактов;
- каждая дата есть среди дат указанных фактов: дата факта по местному времени и даты
  из его текста. День засчитывается только целой датой («18.10», «18.10.2026»,
  «18 октября»), отдельное «18» при дате 18.10 — число, которого в факте нет;
- слова советов и диагнозов («рекомендуется», «диагноз», «расстройство»…) встречаются
  в указанных фактах вне цитаты пациента. Цитата — данные: просьба в ней («врачу нужно
  назначить…») не разрешает модели совет.

Обозначения шкал («PHQ-9», «HAMD-17») вырезаются до поиска чисел: их цифры не числа факта.
Числа словами до 30 в любом падеже и наречия «дважды», «втрое» переводятся в цифры.
Числительное, которое код не переводит («сорок», «полтора»), должно быть в фактах дословно.
"""

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from clin_compass.modules.summary.schemas import (
    MAX_STATEMENT_LENGTH,
    MAX_STATEMENTS,
    SummaryFact,
    SummaryStatement,
)

# Как в проверке цитат RAG (`knowledge.answering`): число с дробной частью или дата.
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_SCALE_CODE = re.compile(r"(?<!\w)[A-ZА-ЯЁ]+-\d+(?!\w)")
_WORD = re.compile(r"[а-я]+")
# Цитата пациента в конце текста факта находки (`facts._finding_fact`).
_QUOTE = re.compile(r"Цитата: «.*\Z", re.DOTALL)
_MONTHS = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)
_MONTH_DATE = re.compile(rf"(?<![\d.,])(\d{{1,2}})\s+({'|'.join(_MONTHS)})(?:\s+(\d{{4}}))?")
# Начала слов советов и диагнозов.
_MARKERS = (
    "рекоменд",
    "следует",
    "необходим",
    "нужн",
    "нужен",
    "стоит",
    "целесообразн",
    "показан",
    "назнач",
    "диагноз",
    "расстройств",
)
# Числительные, которые код не переводит: в фактах они должны быть дословно.
_UNCONVERTED = (
    "сорок",
    "пятьдесят",
    "пятидесят",
    "шестьдесят",
    "шестидесят",
    "семьдесят",
    "семидесят",
    "восемьдесят",
    "восьмидесят",
    "девяност",
    "сотн",
    "двест",
    "двухсот",
    "трист",
    "трехсот",
    "тысяч",
    "полтор",
    "полутор",
    "десятк",
    "дюжин",
    "кратн",
)
_UNCONVERTED_WORDS = frozenset({"сто", "ста"})


def _five_to_thirty() -> dict[str, int]:
    """5–20 и 30: «пять, пяти, пятью»; у «восемь» — ещё «восьми» и «восьмью»."""
    bases = {
        "пять": 5,
        "шесть": 6,
        "семь": 7,
        "девять": 9,
        "десять": 10,
        "одиннадцать": 11,
        "двенадцать": 12,
        "тринадцать": 13,
        "четырнадцать": 14,
        "пятнадцать": 15,
        "шестнадцать": 16,
        "семнадцать": 17,
        "восемнадцать": 18,
        "девятнадцать": 19,
        "двадцать": 20,
        "тридцать": 30,
    }
    forms = {form: v for b, v in bases.items() for form in (b, b[:-1] + "и", b + "ю")}
    return forms | dict.fromkeys(("восемь", "восьми", "восемью", "восьмью"), 8)


_NUMERALS: dict[str, int] = {
    **dict.fromkeys(("ноль", "нуль", "ноля", "нуля", "нолю", "нулю", "нолем", "нулем"), 0),
    **dict.fromkeys(
        ("один", "одна", "одно", "одного", "одной", "одному", "одним", "одном", "одну", "одною"),
        1,
    ),
    **dict.fromkeys(("два", "две", "двух", "двум", "двумя", "двое", "двоих", "двоим"), 2),
    **dict.fromkeys(("три", "трех", "трем", "тремя", "трое", "троих", "троим"), 3),
    **dict.fromkeys(("четыре", "четырех", "четырем", "четырьмя", "четверо", "четверых"), 4),
    **dict.fromkeys(("пятеро", "пятерых"), 5),
    **_five_to_thirty(),
    "дважды": 2,
    "трижды": 3,
    "четырежды": 4,
    "вдвое": 2,
    "втрое": 3,
    "вчетверо": 4,
    "впятеро": 5,
    "вдесятеро": 10,
}

__all__ = ["MAX_STATEMENTS", "StatementOutput", "verify"]


class StatementOutput(BaseModel):
    """Утверждение в ответе модели: текст и номера фактов."""

    text: str
    facts: list[int]


@dataclass(frozen=True)
class _Scan:
    """Что нашлось в тексте: даты «д.м[.гггг]», числа, слова, начала слов-маркеров."""

    dates: frozenset[str]
    numbers: frozenset[str]
    words: frozenset[str]
    unconverted: frozenset[str]
    markers: frozenset[str]


def verify(
    statements: Sequence[StatementOutput], facts: Sequence[SummaryFact], zone: ZoneInfo
) -> tuple[list[SummaryStatement], int]:
    """Утверждения, которые подтверждают факты, — не больше `MAX_STATEMENTS`, и число остальных.

    Отбрасывается утверждение без номеров, с номером не из списка, с пустым или слишком
    длинным текстом, с числом, датой или словом совета и диагноза не из указанных фактов.
    """
    by_number = {f.number: f for f in facts}
    kept: list[SummaryStatement] = []
    for statement in statements:
        checked = _checked(statement, by_number, zone)
        if checked is not None and len(kept) < MAX_STATEMENTS:
            kept.append(checked)
    return kept, len(statements) - len(kept)


def _checked(
    statement: StatementOutput, facts: dict[int, SummaryFact], zone: ZoneInfo
) -> SummaryStatement | None:
    text = statement.text.strip()
    numbers = list(dict.fromkeys(statement.facts))
    if not text or len(text) > MAX_STATEMENT_LENGTH or not numbers:
        return None
    if any(n not in facts for n in numbers):
        return None
    if not _supported(_scan(text), [facts[n] for n in numbers], zone):
        return None
    return SummaryStatement(text=text, facts=numbers)


def _supported(said: _Scan, facts: Iterable[SummaryFact], zone: ZoneInfo) -> bool:
    dates: set[str] = set()
    numbers: set[str] = set()
    words: set[str] = set()
    markers: set[str] = set()
    for fact in facts:
        day = fact.at.astimezone(zone).date()
        scan = _scan(fact.text)
        dates |= _with_years(scan.dates | {_date(day)}, day.year)
        numbers |= scan.numbers
        words |= scan.words
        markers |= _scan(_QUOTE.sub("", fact.text)).markers
    return (
        said.dates <= dates
        and said.numbers <= numbers
        and said.unconverted <= words
        and said.markers <= markers
    )


def _scan(text: str) -> _Scan:
    lowered = _SCALE_CODE.sub(" ", text).lower().replace("ё", "е")
    dates: set[str] = set()

    def spelled(match: re.Match[str]) -> str:
        day, month, year = match.group(1), _MONTHS.index(match.group(2)) + 1, match.group(3)
        if 1 <= int(day) <= 31:
            dates.add(f"{int(day)}.{month}" + (f".{year}" if year else ""))
            return " "
        return match.group(0)

    lowered = _MONTH_DATE.sub(spelled, lowered)
    numbers: set[str] = set()
    for token in _NUMBER.findall(lowered):
        parts = token.replace(",", ".").split(".")
        if _is_date(parts):
            dates.add(".".join(str(int(p)) for p in parts))
        else:
            head, *rest = parts
            numbers.add(".".join([str(int(head)), *rest]))
    words = _WORD.findall(lowered)
    values, unconverted = _numerals(words)
    return _Scan(
        dates=frozenset(dates),
        numbers=frozenset(numbers | values),
        words=frozenset(words),
        unconverted=frozenset(unconverted),
        markers=frozenset(m for w in words for m in _MARKERS if w.startswith(m)),
    )


def _numerals(words: Sequence[str]) -> tuple[set[str], set[str]]:
    """Числа словами в цифрах и числительные, которые код не переводит.

    «двадцать пять» и «двадцати пяти» — 25: десятки с единицами следом складываются.
    """
    values: set[str] = set()
    unconverted: set[str] = set()
    index = 0
    while index < len(words):
        word = words[index]
        value = _NUMERALS.get(word)
        if value is None:
            if word in _UNCONVERTED_WORDS or word.startswith(_UNCONVERTED):
                unconverted.add(word)
            index += 1
            continue
        following = _NUMERALS.get(words[index + 1]) if index + 1 < len(words) else None
        if value in (20, 30) and following is not None and 1 <= following <= 9:
            value += following
            index += 1
        values.add(str(value))
        index += 1
    return values, unconverted


def _with_years(dates: Iterable[str], year: int) -> set[str]:
    """Дата без года засчитывается и с годом факта, дата с годом — и без него."""
    result: set[str] = set()
    for value in dates:
        day_month = ".".join(value.split(".")[:2])
        result |= {day_month, f"{day_month}.{year}" if value == day_month else value}
    return result


def _date(day: date) -> str:
    return f"{day.day}.{day.month}.{day.year}"


def _is_date(parts: list[str]) -> bool:
    """«дд.мм» или «дд.мм.гггг»: день 1–31, месяц двумя цифрами 01–12."""
    if len(parts) not in (2, 3) or len(parts[0]) > 2 or len(parts[1]) != 2:
        return False
    if len(parts) == 3 and len(parts[2]) != 4:
        return False
    return 1 <= int(parts[0]) <= 31 and 1 <= int(parts[1]) <= 12
