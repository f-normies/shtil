"""Факторы поиска для подбора (спека #70, 5.3): чистые функции без сети и базы."""

import pytest

from variants import (
    Abbreviations,
    chosen_sources,
    document_text,
    parse_abbreviations,
    split_parts,
)

LINES_HTML = (
    "<p>АД – антидепрессант</p><p>ДЭ – депрессивный эпизод</p>"
    "<p>ЭЭГ – регистрация электрокардиограммы</p><p>ИМАО-А – ингибиторы моноаминоксидазы типа А</p>"
)
TABLE_HTML = (
    "<table><tr><td>ГТР</td><td>– генерализованное тревожное расстройство</td></tr>"
    "<tr><td>СИОЗС</td><td>– селективные ингибиторы обратного захвата серотонина</td></tr>"
    "<tr><td>HARS</td><td>– ((Eng.) The Hamilton Anxiety Rating Sсаle) – "
    "шкала тревоги Гамильтона</td>"
    "</tr></table>"
)


def test_parse_abbreviations_from_lines() -> None:
    parsed = parse_abbreviations(LINES_HTML)

    assert parsed["АД"] == "антидепрессант"
    assert parsed["ИМАО-А"] == "ингибиторы моноаминоксидазы типа А"


def test_parse_abbreviations_from_table_cells() -> None:
    parsed = parse_abbreviations(TABLE_HTML)

    assert parsed["ГТР"] == "генерализованное тревожное расстройство"
    assert parsed["HARS"] == "((Eng.) The Hamilton Anxiety Rating Sсаle) – шкала тревоги Гамильтона"


def test_fixes_override_source_entries() -> None:
    abbreviations = Abbreviations.build(
        [parse_abbreviations(LINES_HTML)], {"ЭЭГ": "электроэнцефалография"}
    )

    assert abbreviations.full["ЭЭГ"] == "электроэнцефалография"


@pytest.mark.parametrize(
    ("question", "found"),
    [
        (
            "Нужна ли ЭЭГ при ГТР?",
            ["электроэнцефалография", "генерализованное тревожное расстройство"],
        ),
        ("Что такое ДЭ-подобные состояния?", []),
        ("Назначать ли ад при гипертонии?", []),
        (
            "Сочетать ли ИМАО-А с СИОЗС?",
            [
                "ингибиторы моноаминоксидазы типа А",
                "селективные ингибиторы обратного захвата серотонина",
            ],
        ),
    ],
)
def test_expansions_match_whole_words_case_sensitive(question: str, found: list[str]) -> None:
    abbreviations = Abbreviations.build(
        [parse_abbreviations(LINES_HTML), parse_abbreviations(TABLE_HTML)],
        {"ЭЭГ": "электроэнцефалография"},
    )

    assert abbreviations.expansions(question) == found


def test_expanded_queries_for_embedding_and_full_text() -> None:
    abbreviations = Abbreviations.build([parse_abbreviations(TABLE_HTML)], {})

    assert abbreviations.for_embedding("ГТР у подростка?") == (
        "ГТР у подростка? (генерализованное тревожное расстройство)"
    )
    assert abbreviations.for_full_text("ГТР у подростка?") == (
        "ГТР у подростка? генерализованное тревожное расстройство"
    )
    assert abbreviations.for_embedding("Без сокращений?") == "Без сокращений?"


@pytest.mark.parametrize(
    ("question", "sources"),
    [
        ("Можно ли Авиандр при беременности?", {"aviandr-ohlp"}),
        ("Доза маритупирдина у пожилых?", {"aviandr-ohlp"}),
        ("Длительность терапии ГТР?", {"kr-gad-2024"}),
        ("Генерализованная тревога и Авиандр", {"kr-gad-2024", "aviandr-ohlp"}),
        ("Что делать при бессоннице?", set()),
    ],
)
def test_chosen_sources_by_word_prefixes(question: str, sources: set[str]) -> None:
    terms = {
        "aviandr-ohlp": ["авиандр", "маритупирдин"],
        "kr-gad-2024": ["гтр", "генерализованн"],
        "kr-depression-2024": ["депресс", "дэ", "рдр"],
    }

    assert chosen_sources(question, terms) == sources


def test_short_fragment_is_one_part() -> None:
    assert split_parts("Короткий текст.", threshold=1500, limit=1200) == ["Короткий текст."]


def test_long_fragment_splits_by_paragraphs_then_sentences() -> None:
    paragraph = "Первое предложение абзаца. " * 30  # ~810 знаков
    long_paragraph = "Длинное предложение без конца абзаца. " * 60  # ~2280 знаков
    text = f"{paragraph.strip()}\n{paragraph.strip()}\n{long_paragraph.strip()}"

    parts = split_parts(text, threshold=1500, limit=1200)

    assert all(len(part) <= 1200 for part in parts)
    assert parts[0] == paragraph.strip()
    assert " ".join(parts).split() == text.split()


def test_document_text_puts_context_before_heading() -> None:
    assert document_text("КР ГТР", "Диагностика / ЭЭГ", "Текст.", None) == (
        "КР ГТР. Диагностика / ЭЭГ\nТекст."
    )
    assert document_text("КР ГТР", "Диагностика / ЭЭГ", "Текст.", "О показаниях к ЭЭГ.") == (
        "КР ГТР. Диагностика / ЭЭГ\nО показаниях к ЭЭГ.\nТекст."
    )
