"""Текст документа из страниц docparse: источники страниц, таблицы, цитаты (спека #30, 3.3)."""

import pytest

from clin_compass.core.ai.docparse import ParsedPage
from clin_compass.modules.ingestion.document import (
    document_text,
    flatten_tables,
    quote_found,
    quote_page,
    text_problem,
)

SCAN = (
    "Выписной эпикриз. Больная К., 27 лет, находилась на лечении с 02.09.2026 по 07.10.2026. "
    "Диагноз: депрессивный эпизод тяжёлой степени без психотических симптомов (F32.2)."
)
HTML_TABLE = (
    "Шкала HAMD-17:\n\n<table><tr><th>№</th><th>Пункт</th><th>Балл</th></tr>"
    "<tr><td>3</td><td>Суицидальные<br>намерения</td><td>1</td></tr>"
    "<tr><td>4</td><td>Бессонница &amp; тревога</td><td>2</td></tr></table>\n\nСумма 18."
)
MARKDOWN_TABLE = (
    "Назначения:\n\n| Препарат | Доза | Режим |\n|---|---|---|\n"
    "| Метформин | 1000 мг | 2 раза в день |\n| Агомелатин | 25 мг | на ночь |\n\nКонец."
)


def page(number: int, text: str, layer: str = "") -> ParsedPage:
    return ParsedPage(number=number, text=text, layer_text=layer)


@pytest.mark.parametrize("layer", ["Страница 1 из 2", "КОПИЯ ВЕРНА\r\nПодпись"])
def test_page_whose_layer_is_a_stamp_takes_model_text(layer: str) -> None:
    # Скан с колонтитулом или штампом в слое: в слое меньше половины слов модели.
    built = document_text([page(1, SCAN, layer)])

    assert built.text == SCAN
    assert [p.source for p in built.pages] == ["ocr"]
    assert built.ocr is True


def test_page_with_full_layer_that_did_not_reconcile_takes_layer() -> None:
    layer = SCAN.replace("Выписной эпикриз.", "Выписка.").replace("тяжёлой", "тяжелой")
    lost = "Выписной эпикриз. Что-то совсем иное без слов слоя: альфа бета гамма дельта эпсилон."

    built = document_text([page(1, lost + " " + SCAN[:40], layer)])

    assert [p.source for p in built.pages] == ["layer"]
    assert built.text == layer


def test_html_table_becomes_one_line_per_row() -> None:
    assert flatten_tables(HTML_TABLE) == (
        "Шкала HAMD-17:\n\n№ | Пункт | Балл\n3 | Суицидальные намерения | 1\n"
        "4 | Бессонница & тревога | 2\n\nСумма 18."
    )


def test_markdown_table_becomes_one_line_per_row() -> None:
    assert flatten_tables(MARKDOWN_TABLE) == (
        "Назначения:\n\nПрепарат | Доза | Режим\nМетформин | 1000 мг | 2 раза в день\n"
        "Агомелатин | 25 мг | на ночь\n\nКонец."
    )


def test_ocr_page_has_tables_latex_and_entities_resolved() -> None:
    text = "АД 128/84, СКФ \\( \\geq 60 \\) &amp; норма.\n\n" + HTML_TABLE

    built = document_text([page(1, text)])

    assert "СКФ ≥ 60 & норма" in built.text
    assert "3 | Суицидальные намерения | 1" in built.text
    assert "<" not in built.text and "&amp;" not in built.text


@pytest.mark.parametrize(
    "quote",
    [
        "3 | Суицидальные намерения | 1",
        "Суицидальные намерения | 1",
        "Суицидальные намерения 1",
        "Метформин | 1000 мг | 2 раза в день",
    ],
)
def test_table_row_quote_is_found(quote: str) -> None:
    built = document_text([page(1, HTML_TABLE), page(2, MARKDOWN_TABLE)])

    assert quote_found(quote, built.text)
    assert quote_page(quote, built.text, built.pages) in {1, 2}


def test_made_up_row_is_not_found() -> None:
    built = document_text([page(1, HTML_TABLE)])

    assert not quote_found("Суицидальные намерения | 2", built.text)


HISTORY = (
    "Амбулаторно принимала сертралин 100 мг в сутки в течение 8 недель — без эффекта.\n"
    "В отделении осмотрена психиатром. Сертралин отменён. Назначен венлафаксин 75 мг.\n"
    "Рекомендовано: продолжить венлафаксин 75 мг 3 раза в день."
)


@pytest.mark.parametrize(
    "quote",
    [
        # Модель склеивает цитату из нескольких мест, хотя промпт это запрещает.
        "в течение 8 недель — без эффекта. Сертралин отменён.",
        "Назначен венлафаксин 75 мг. Продолжить венлафаксин 75 мг 3 раза в день",
        "принимала сертралин 100 мг … Сертралин отменён",
        "принимала сертралин 100 мг ... Сертралин отменён",
    ],
)
def test_quote_glued_from_several_places_is_found_part_by_part(quote: str) -> None:
    assert quote_found(quote, HISTORY)


@pytest.mark.parametrize(
    "quote",
    [
        "принимала сертралин 100 мг … Сертралин заменён на флуоксетин",
        "Назначен венлафаксин 75 мг. Продолжить венлафаксин 150 мг",
    ],
)
def test_glued_quote_with_a_made_up_part_is_not_found(quote: str) -> None:
    assert not quote_found(quote, HISTORY)


@pytest.mark.parametrize(
    ("text", "problem"),
    [("", "empty_text"), ("  \n ", "empty_text"), ("а" * 15_001, "text_too_long"), ("а", None)],
)
def test_text_limits(text: str, problem: str | None) -> None:
    assert text_problem(text) == problem
