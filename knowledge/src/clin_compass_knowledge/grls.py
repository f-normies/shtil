"""Сверка названий препаратов со снимком ГРЛС (спека #32, раздел 5.4).

Снимок — `sources/files/grls.csv`: номер удостоверения, торговое наименование, МНН и статус.
Собирает его `grls_update`. Падежи снимает вызывающий (модель отдаёт начальную форму),
здесь ловятся опечатки и разное написание.
"""

import csv
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, get_args

from rapidfuzz import fuzz, process

RegistryStatus = Literal["active", "unknown", "suspended", "inactive"]
Check = Literal["registered", "unknown", "suspended", "inactive", "similar", "not_found"]

# Лучший статус названия — первый в этом порядке среди его удостоверений.
STATUS_ORDER: tuple[RegistryStatus, ...] = get_args(RegistryStatus)
_CHECK_BY_STATUS: dict[RegistryStatus, Check] = {
    "active": "registered",
    "unknown": "unknown",
    "suspended": "suspended",
    "inactive": "inactive",
}
# Порог нечёткого совпадения (`fuzz.ratio`, 0–100) и разброс вариантов от лучшего.
# Подобраны на выгрузке от 16.10.2026: «авиандор» → 93, «ципролекс» → 89 и 88.
SIMILAR_MIN_SCORE = 85.0
SIMILAR_SPREAD = 3.0
MAX_MATCHES = 3
# Ключ по первому слову названия — от 4 букв: «но» из «Но-шпа» совпало бы с чем угодно.
_FIRST_WORD_MIN_LEN = 4

# Слова, которые названием препарата не являются: форма, упаковка, единицы дозы, общие слова.
# Модель отдаёт название в начальной форме, поэтому падежей здесь почти нет.
_NOT_A_NAME = frozenset(
    {
        "таблетка", "таблетки", "таблеток", "таблетку", "капсула", "капсулы", "капли",
        "мазь", "крем", "гель", "сироп", "раствор", "порошок", "укол", "уколы", "спрей",
        "лекарство", "лекарства", "препарат", "препараты", "витамин", "витамины", "пилюли",
        "мг", "мл", "мкг", "г", "шт",
    }
)  # fmt: skip

# Вид ключа: при точном совпадении побеждает меньший. МНН важнее первого слова
# торгового наименования: «сертралин» — это МНН, а не «Сертралин-Тест».
_TRADE, _INN, _FIRST_WORD = 0, 1, 2


@dataclass(frozen=True)
class RegistryEntry:
    """Строка снимка: одно удостоверение одного торгового наименования."""

    reg_number: str
    trade_name: str
    inn: str
    status: RegistryStatus


@dataclass(frozen=True)
class DrugMatch:
    """Найденная запись. При совпадении по МНН торгового наименования и номера нет."""

    trade_name: str | None
    inn: str
    reg_number: str | None


@dataclass(frozen=True)
class DrugCheck:
    """Итог сверки названия `name`.

    `inn` — МНН точного совпадения, если он один у всех удостоверений названия с любым
    статусом. Пустой МНН снимка не считается. Разные МНН или похожее название — `None`.
    `inn_options` — из чего тогда выбирает врач: все разные МНН точного совпадения с любым
    статусом или МНН похожих названий. Без повторов и без обрезки до `MAX_MATCHES`.
    """

    name: str
    check: Check
    matches: tuple[DrugMatch, ...]
    inn: str | None = None
    inn_options: tuple[str, ...] = ()


def drug_name(raw: str) -> str:
    """Название без дозы и формы: «Авиандр 50 мг» → «Авиандр». Одни общие слова — пусто.

    Слова-формы («таблетки», «мг») пропускаются. На дозе («50», «50мг») название кончается.
    Слово с цифрой после букв («B6», «Q10») кончает название, только если оно уже есть:
    «Магне B6» → «Магне», а «омега-3» остаётся названием, и врач увидит «нет в ГРЛС».
    """
    words: list[str] = []
    for word in raw.split():
        if word[0].isdigit():
            break
        if normalize(word) in _NOT_A_NAME:
            continue
        if words and any(ch.isdigit() for ch in word):
            break
        words.append(word)
    return " ".join(words)


def normalize(name: str) -> str:
    """Ключ сравнения: нижний регистр, «ё» → «е», только буквы, цифры и «+»."""
    lowered = name.lower().replace("ё", "е")
    return "".join(ch for ch in lowered if ch.isalnum() or ch == "+")


class Registry:
    """Индекс снимка ГРЛС по торговым наименованиям, их первым словам, МНН и номерам РУ."""

    def __init__(self, entries: Iterable[RegistryEntry]) -> None:
        self._index: dict[str, tuple[int, list[RegistryEntry]]] = {}
        self._by_number: dict[str, RegistryEntry] = {}
        for entry in entries:
            self._by_number.setdefault(entry.reg_number, entry)
            self._add(normalize(entry.trade_name), _TRADE, entry)
            self._add(normalize(entry.inn), _INN, entry)
            words = entry.trade_name.replace("-", " ").split()
            first = normalize(words[0]) if words else ""
            if len(first) >= _FIRST_WORD_MIN_LEN:
                self._add(first, _FIRST_WORD, entry)
        self._keys = list(self._index)

    def _add(self, key: str, kind: int, entry: RegistryEntry) -> None:
        if not key:
            return
        current = self._index.get(key)
        if current is None or kind < current[0]:
            self._index[key] = (kind, [entry])
        elif kind == current[0]:
            current[1].append(entry)

    def by_number(self, number: str) -> RegistryEntry | None:
        """Запись по номеру регистрационного удостоверения, как он записан в снимке."""
        return self._by_number.get(number.strip())

    def lookup(self, name: str) -> DrugCheck:
        """Сверка названия: точное совпадение, иначе близкие названия, иначе «нет в ГРЛС».

        Сначала название целиком («Магне B6»), потом без дозы и формы («Авиандр 50 мг»).
        """
        found = self._index.get(normalize(name))
        key = normalize(drug_name(name))
        if found is None and key:
            found = self._index.get(key)
        if found is not None:
            kind, entries = found
            best = _best_status(entries)
            matches = _matches(kind, [e for e in entries if e.status == best])
            inns = _distinct_inns(e.inn for e in entries)
            inn = inns[0] if len(inns) == 1 else None
            options = () if inn else inns
            return DrugCheck(name, _CHECK_BY_STATUS[best], matches[:MAX_MATCHES], inn, options)
        if not key:
            return DrugCheck(name, "not_found", ())
        scored = process.extract(
            key, self._keys, scorer=fuzz.ratio, limit=10, score_cutoff=SIMILAR_MIN_SCORE
        )
        if not scored:
            return DrugCheck(name, "not_found", ())
        top = scored[0][1]
        similar: list[DrugMatch] = []
        for candidate, score, _ in scored:
            if score < top - SIMILAR_SPREAD:
                break
            kind, entries = self._index[candidate]
            best = _best_status(entries)
            for match in _matches(kind, [e for e in entries if e.status == best])[:1]:
                if match not in similar:
                    similar.append(match)
        shown = tuple(similar[:MAX_MATCHES])
        return DrugCheck(name, "similar", shown, None, _distinct_inns(m.inn for m in shown))


def _best_status(entries: list[RegistryEntry]) -> RegistryStatus:
    return min((e.status for e in entries), key=STATUS_ORDER.index)


def _distinct_inns(inns: Iterable[str]) -> tuple[str, ...]:
    """Разные МНН в порядке снимка, в написании первого из них; пустые МНН не считаются."""
    seen: dict[str, str] = {}
    for inn in inns:
        if normalize(inn):
            seen.setdefault(normalize(inn), inn.strip())
    return tuple(seen.values())


def _matches(kind: int, entries: list[RegistryEntry]) -> tuple[DrugMatch, ...]:
    """Записи для врача без повторов, в порядке снимка."""
    if kind == _INN:
        return (DrugMatch(None, entries[0].inn, None),)
    result: list[DrugMatch] = []
    for e in entries:
        match = DrugMatch(e.trade_name, e.inn, e.reg_number)
        if match not in result:
            result.append(match)
    return tuple(result)


def load_registry(path: Path) -> Registry:
    """Снимок ГРЛС из CSV. Неизвестный статус — `ValueError` с номером строки."""
    with path.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    entries = []
    for line, row in enumerate(rows, start=2):
        status = row["status"]
        if status not in STATUS_ORDER:
            raise ValueError(f"{path.name}: строка {line}: неизвестный статус {status}")
        entries.append(RegistryEntry(row["reg_number"], row["trade_name"], row["inn"], status))
    return Registry(entries)
