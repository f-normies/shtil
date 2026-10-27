"""Части фрагмента для цитаты по номерам (спека #70, 6.1): предложения, пункты, строки таблиц."""

import pytest

from clin_compass.modules.knowledge.sentences import quote_span, units


def _texts(text: str) -> list[str]:
    return [text[start:end] for start, end in units(text)]


def test_sentences_split_on_capital_after_full_stop() -> None:
    text = "Первое предложение. Второе предложение! Третье?"

    assert _texts(text) == ["Первое предложение.", "Второе предложение!", "Третье?"]


@pytest.mark.parametrize(
    "text",
    [
        "Принимать по 20 мг/сут. в течение 4 нед., т. е. около месяца.",
        "Доза 0.5 мг или 1,5 мг в сутки.",
        "См. раздел 4.3 и др. разделы.",
    ],
)
def test_abbreviations_and_numbers_do_not_split(text: str) -> None:
    assert _texts(text) == [text]


def test_reference_brackets_end_sentence_before_levels_line() -> None:
    text = "Рекомендуется ЭЭГ [15, 16]. Уровень убедительности рекомендаций – С."

    assert _texts(text) == [
        "Рекомендуется ЭЭГ [15, 16].",
        "Уровень убедительности рекомендаций – С.",
    ]


def test_lines_list_items_and_table_rows_are_separate_units() -> None:
    text = (
        "Противопоказания:\n- беременность;\n- возраст до 18 лет.\n\n"
        "Часто | головная боль\nРедко | сыпь"
    )

    assert _texts(text) == [
        "Противопоказания:",
        "- беременность;",
        "- возраст до 18 лет.",
        "Часто | головная боль",
        "Редко | сыпь",
    ]


def test_quote_span_is_exact_slice_from_first_to_last_unit() -> None:
    text = "Первое. Второе предложение.\nТретье."
    spans = units(text)

    assert quote_span(text, spans, 2, 3) == "Второе предложение.\nТретье."
    assert quote_span(text, spans, 1, 1) == "Первое."


@pytest.mark.parametrize(("first", "last"), [(0, 1), (2, 1), (1, 4), (3, 3)])
def test_quote_span_rejects_ranges_outside_units(first: int, last: int) -> None:
    text = "Первое. Второе."

    assert quote_span(text, units(text), first, last) is None
