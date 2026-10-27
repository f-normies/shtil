"""Разбор `locator` из knowledge/ в номера разделов (спека #106, 3.1).

У каждого случая — файл knowledge/, откуда взят пример.
"""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import clin_compass_knowledge
import pytest
import yaml

from clin_compass.modules.knowledge.sections import parse_locator

KNOWLEDGE = Path(clin_compass_knowledge.__file__).resolve().parents[2]
# Источники, тексты которых лежат в индексе базы знаний: у их ссылок с цифрой в начале
# должен найтись номер раздела.
INDEXED = {"aviandr-ohlp", "kr-depression-2024", "kr-gad-2024"}


@pytest.mark.parametrize(
    ("locator", "numbers"),
    [
        # drugs/aviandr.yaml, contraindications
        ("4.3", ("4.3",)),
        # drugs/aviandr.yaml, почечная недостаточность
        ("4.3; 4.2, «Пациенты с почечной недостаточностью»", ("4.3", "4.2")),
        # drugs/aviandr.yaml, ингибиторы МАО
        ("4.5, «Ингибиторы моноаминоксидазы (МАО)»", ("4.5",)),
        # drugs/aviandr.yaml, случай с СКФ
        ("4.3: СКФ < 60 мл/мин", ("4.3",)),
        # drugs/aviandr.yaml, нежелательные реакции
        ("4.8, таблица 1", ("4.8",)),
        # drugs/aviandr.yaml: после «;» — пояснение без номера, раздела в нём нет
        (
            "4.5: ингибиторы CYP2D6; бупропион — сильный ингибитор CYP2D6, решение медика"
            " (ревью #76)",
            ("4.5",),
        ),
        # drugs/aviandr.yaml, нежелательные реакции: листок-вкладыш
        ("раздел 4, «Немедленно сообщите лечащему врачу»", ("4",)),
        # rules/hamd17-suicidal-intent.yaml
        ("раздел 3.1.3: ДЭ с высоким суицидальным риском — лечение в стационаре", ("3.1.3",)),
        # rules/routing-depression-severe.yaml
        ("раздел 6: ДЭ тяжёлой степени — показание к стационарному лечению", ("6",)),
        # signs/self-harm.yaml
        ("раздел 1.6, идеи или действия, направленные на самоповреждение или суицид", ("1.6",)),
        # rules/routing-anxiety-severe.yaml: приложение — не раздел
        ("прил. Г1, ключ: HARS 25–56 — тяжёлая степень", ()),
        # rules/routing-anxiety-probable.yaml: страница — не раздел
        ("с. 39: GAD-7 ≥ 10 — вероятный диагноз ГТР", ()),
        # rules/phq9-suicidal-thoughts.yaml: «пункт» не в начале части
        ("прил., с. 121, пункт 9", ()),
        # drugs/aviandr.yaml, регистрация: номер РУ — не раздел
        ("ЛП-№(002445)-(РГ-RU), АВИАНДР®, статус active", ()),
        # Сокращения «п.» и «пункт» из спеки #106, 3.1; в knowledge/ их пока нет.
        ("п. 4.3", ("4.3",)),
        ("пункт 4.2, «Способ применения»", ("4.2",)),
        # Повтор раздела отдаётся один раз.
        ("4.3; 4.3: СКФ < 60 мл/мин", ("4.3",)),
    ],
)
def test_locator_gives_section_numbers(locator: str, numbers: tuple[str, ...]) -> None:
    assert parse_locator(locator) == numbers


def _refs(node: Any) -> Iterator[tuple[str, str]]:
    """Все пары (источник, locator) в YAML: ссылки правил, признаков, препарата и их случаев."""
    if isinstance(node, dict):
        if isinstance(node.get("source"), str) and isinstance(node.get("locator"), str):
            yield node["source"], node["locator"]
        for value in node.values():
            yield from _refs(value)
    elif isinstance(node, list):
        for item in node:
            yield from _refs(item)


def knowledge_refs() -> list[tuple[str, str]]:
    """Ссылки из knowledge/rules, knowledge/drugs и knowledge/signs без повторов."""
    refs: dict[tuple[str, str], None] = {}
    for folder in ("rules", "drugs", "signs"):
        for path in sorted((KNOWLEDGE / folder).glob("*.yaml")):
            refs.update(dict.fromkeys(_refs(yaml.safe_load(path.read_text(encoding="utf-8")))))
    return list(refs)


def test_every_knowledge_locator_is_parsed() -> None:
    refs = knowledge_refs()
    assert len(refs) > 50

    for source, locator in refs:
        numbers = parse_locator(locator)
        # Ссылка на индексированный документ, которая начинается с номера или со слова
        # «раздел», даёт номер раздела.
        if source in INDEXED and locator.lstrip()[:1].isdigit():
            assert numbers, locator
        if locator.startswith("раздел"):
            assert numbers, locator
