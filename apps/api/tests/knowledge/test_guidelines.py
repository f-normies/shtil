"""Разбор клинической рекомендации из API рубрикатора (спека #27, 3.2).

HTML синтетический, той же структуры, что у рубрикатора: пункт — `<li>` со словом
«рекомендуется», строка уровней — отдельный абзац, комментарий — абзацы после неё.
Настоящий текст рекомендаций в репозиторий не кладём: условия копирования не указаны.
"""

from typing import Any

import pytest

from clin_compass.modules.knowledge.fragments import ParsedFragment, ParseError
from clin_compass.modules.knowledge.guidelines import parse_guideline

A1 = "Уровень убедительности рекомендаций – A (уровень достоверности доказательств – 1)."
# Кириллическая «В» и строка без тире после слова «рекомендаций».
B3 = "Уровень убедительности рекомендаций В (уровень достоверности доказательств – 3)"
# «рекомендации», «Уровень» с заглавной, кириллическая «С», точка после жирного.
C5 = "Уровень убедительности рекомендации С (Уровень достоверности доказательств –5)."
LEAD = "Пациентам с резистентностью рекомендовано добавить:"
YOTA_COMMENT = "при переходе рекомендуется снижать дозу Йоты."

Row = tuple[str, str | None, int | None]


def _li(html: str) -> str:
    return f"<ul><li><p>{html}</p></li></ul>"


def _p(html: str) -> str:
    return f"<p>{html}</p>"


def _grade(line: str) -> str:
    if line.endswith(")."):
        return f"<p><strong>{line[:-1]}</strong>.</p>"
    return f"<p><strong>{line}</strong></p>"


def _guideline(*sections: tuple[str, str, str | None]) -> dict[str, Any]:
    return {
        "id": "900_1",
        "obj": {"sections": [{"id": i, "title": t, "content": c} for i, t, c in sections]},
    }


def _treatment(html: str) -> list[ParsedFragment]:
    return parse_guideline(_guideline(("doc_3", "3. Лечение", html)))


def _rows(fragments: list[ParsedFragment]) -> list[tuple[str, Row]]:
    return [(f.kind, (f.text, f.uur, f.udd)) for f in fragments]


def _rec(*lines: str, uur: str, udd: int) -> tuple[str, Row]:
    return ("recommendation", ("\n".join(lines), uur, udd))


def _text(*lines: str) -> tuple[str, Row]:
    return ("text", ("\n".join(lines), None, None))


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        pytest.param(
            _p("Вводный абзац.")
            + _li("Пациентам <strong>рекомендуется</strong> Альфа 10 мг/сут [1].")
            + _grade(A1)
            + "<p><strong>Комментарии.</strong><em> Альфу дают утром; при плохой переносимости"
            " рекомендуется снизить дозу.</em></p>"
            + _p("<em>Второй абзац комментария.</em>")
            + "<h2>3.1 Этап</h2>"
            + _p("Текст после заголовка."),
            [
                _text("Вводный абзац."),
                _rec(
                    "Пациентам рекомендуется Альфа 10 мг/сут [1].",
                    A1,
                    "Комментарии. Альфу дают утром; при плохой переносимости рекомендуется"
                    " снизить дозу.",
                    "Второй абзац комментария.",
                    uur="A",
                    udd=1,
                ),
                _text("Текст после заголовка."),
            ],
            id="строка уровней с тире, «Комментарии.» до заголовка",
        ),
        pytest.param(
            _li("Пациентам <strong>рекомендуется</strong> Бета [2].")
            + _grade(B3)
            + "<p><strong><em>Комментарий:</em></strong><em> Бета действует медленно.</em></p>"
            + _li("Пациентам с язвой <strong>не рекомендуется</strong> Гамма [3].")
            + _grade(C5),
            [
                _rec(
                    "Пациентам рекомендуется Бета [2].",
                    B3,
                    "Комментарий: Бета действует медленно.",
                    uur="B",
                    udd=3,
                ),
                _rec("Пациентам с язвой не рекомендуется Гамма [3].", C5, uur="C", udd=5),
            ],
            id="строка уровней без тире, «Комментарий:», «не рекомендуется»",
        ),
        pytest.param(
            _p("Обычно рекомендуется наблюдение.")
            + _li("Взрослым <strong>рекомендовано</strong> Дельта 5 мг.")
            + _grade(A1),
            [
                _text("Обычно рекомендуется наблюдение."),
                _rec("Взрослым рекомендовано Дельта 5 мг.", A1, uur="A", udd=1),
            ],
            id="пункт начинается с ближайшего блока со словом",
        ),
        pytest.param(
            _li("Пациентам <strong>рекомедуется</strong> Кси.") + _grade(A1),
            [_rec("Пациентам рекомедуется Кси.", A1, uur="A", udd=1)],
            id="опечатка «рекомедуется» в источнике",
        ),
        pytest.param(
            _li("Пациентам <strong>рекомендуется</strong> один из препаратов:")
            + "<ul><li><p>Эпсилон 5 мг;</p></li><li><p>Дзета 10 мг.</p></li></ul>"
            + _grade(A1),
            [
                _rec(
                    "Пациентам рекомендуется один из препаратов:",
                    "Эпсилон 5 мг;",
                    "Дзета 10 мг.",
                    A1,
                    uur="A",
                    udd=1,
                )
            ],
            id="пункт из нескольких блоков",
        ),
        pytest.param(
            _li(LEAD)
            + _p("- Эта 5 мг [4];")
            + _grade(B3)
            + _p("- или Тета 2 мг [5].")
            + _grade(A1)
            + "<p><strong>Комментарии:</strong> <em>Тету отменяют постепенно.</em></p>"
            + _p("<em>Ещё абзац комментария.</em>"),
            [
                _rec(LEAD, "- Эта 5 мг [4];", B3, uur="B", udd=3),
                _rec(
                    LEAD,
                    "- или Тета 2 мг [5].",
                    A1,
                    "Комментарии: Тету отменяют постепенно.",
                    "Ещё абзац комментария.",
                    uur="A",
                    udd=1,
                ),
            ],
            id="варианты пункта со своими уровнями",
        ),
        pytest.param(
            _li(LEAD)
            + _p("- Йота [6];")
            + _grade(A1)
            + _p("Комментарий: Йоту дают вечером.")
            + _p("- Каппа [7];")
            + _grade(B3),
            [
                _rec(LEAD, "- Йота [6];", A1, "Комментарий: Йоту дают вечером.", uur="A", udd=1),
                _rec(LEAD, "- Каппа [7];", B3, uur="B", udd=3),
            ],
            id="комментарий между вариантами",
        ),
        pytest.param(
            _li("Пациентам <strong>рекомендуется</strong> Лямбда.")
            + _grade(A1)
            + _p("<strong>Комментарий:</strong> о Лямбде.")
            + _li("Пациентам <strong>рекомендуется</strong> Мю, уровня нет.")
            + _p("<strong>Комментарий:</strong> о Мю.")
            + _li("Пациентам <strong>рекомендуется</strong> Ню.")
            + _grade(B3),
            [
                _rec(
                    "Пациентам рекомендуется Лямбда.", A1, "Комментарий: о Лямбде.", uur="A", udd=1
                ),
                _text("Пациентам рекомендуется Мю, уровня нет.", "Комментарий: о Мю."),
                _rec("Пациентам рекомендуется Ню.", B3, uur="B", udd=3),
            ],
            id="пункт без строки уровней — текст, на нём кончается комментарий",
        ),
        pytest.param(
            _li(LEAD)
            + _p("- Йота [6];")
            + _grade(A1)
            + _p(f"<strong>Комментарий:</strong> <em>{YOTA_COMMENT}</em>")
            + _p("- или Каппа [7];")
            + _grade(B3),
            [
                _rec(
                    LEAD,
                    "- Йота [6];",
                    A1,
                    f"Комментарий: {YOTA_COMMENT}",
                    uur="A",
                    udd=1,
                ),
                _rec(LEAD, "- или Каппа [7];", B3, uur="B", udd=3),
            ],
            id="«рекомендуется» в комментарии перед вариантом",
        ),
        pytest.param(
            _li(LEAD)
            + _p("- Йота [6];")
            + _grade(A1)
            + _p("<strong>Комментарий:</strong> <em>при переходе рекомендуется снижать дозу.</em>")
            + _p("<em>Продолжение комментария.</em>")
            + _p("Обычный абзац между вариантами.")
            + _p("- или Каппа [7];")
            + _grade(B3),
            [
                _rec(
                    LEAD,
                    "- Йота [6];",
                    A1,
                    "Комментарий: при переходе рекомендуется снижать дозу.",
                    "Продолжение комментария.",
                    uur="A",
                    udd=1,
                ),
                _text("Обычный абзац между вариантами."),
                _rec(LEAD, "- или Каппа [7];", B3, uur="B", udd=3),
            ],
            id="обычный абзац после комментария перед вариантом",
        ),
        pytest.param(
            _li("Пациентам <strong>рекомендуется</strong> Лямбда.")
            + _grade(A1)
            + "<p><strong><em>Комментарий:</em></strong><em> о Лямбде.</em></p>"
            + _p("<em>Продолжение комментария.</em>")
            + _p("<strong>Хирургическое лечение</strong>")
            + _p("Обычный абзац о хирургическом лечении.")
            + _li("Пациентам <strong>рекомендуется</strong> Ню.")
            + _grade(B3),
            [
                _rec(
                    "Пациентам рекомендуется Лямбда.",
                    A1,
                    "Комментарий: о Лямбде.",
                    "Продолжение комментария.",
                    uur="A",
                    udd=1,
                ),
                _text("Хирургическое лечение", "Обычный абзац о хирургическом лечении."),
                _rec("Пациентам рекомендуется Ню.", B3, uur="B", udd=3),
            ],
            id="обычный абзац после комментария не уходит в пункт",
        ),
    ],
)
def test_recommendation_items(html: str, expected: list[tuple[str, Row]]) -> None:
    assert _rows(_treatment(html)) == expected


def test_section_and_heading_path() -> None:
    html = (
        _p("Вступление.")
        + "<h2>3.1 Этап</h2>"
        + _li("Пациентам <strong>рекомендуется</strong> Омикрон.")
        + _grade(A1)
        + "<h3>3.1.1 Подэтап</h3>"
        + _p("Текст подэтапа.")
        + "<h2>3.2 Второй этап</h2>"
        + _p("Ещё текст.")
    )

    fragments = _treatment(html)

    assert [(f.kind, f.section, f.section_title, f.heading_path) for f in fragments] == [
        ("text", "doc_3", "3. Лечение", "3. Лечение"),
        ("recommendation", "doc_3", "3. Лечение", "3. Лечение > 3.1 Этап"),
        ("text", "doc_3", "3. Лечение", "3. Лечение > 3.1 Этап > 3.1.1 Подэтап"),
        ("text", "doc_3", "3. Лечение", "3. Лечение > 3.2 Второй этап"),
    ]


def test_text_is_packed_up_to_2000_chars_at_block_boundaries() -> None:
    paragraphs = ["а" * 900, "б" * 900, "в" * 900]

    fragments = _treatment("".join(_p(text) for text in paragraphs))

    assert [f.text for f in fragments] == ["а" * 900 + "\n" + "б" * 900, "в" * 900]


def test_table_rows_with_header_in_each_part() -> None:
    body = "".join(
        f"<tr><td><p>Препарат {i}</p></td><td><p>{i}–{i + 5}</p></td></tr>" for i in range(150)
    )
    html = (
        _p("<strong>Таблица 2. Дозы</strong>")
        + "<table><thead><tr><th>Препарат</th><th>Доза, мг/сут</th></tr></thead>"
        + f"<tbody>{body}</tbody></table>"
    )

    fragments = parse_guideline(_guideline(("doc_a3", "Приложение А3. Справочные материалы", html)))

    assert fragments[0] == ParsedFragment(
        "doc_a3",
        "Приложение А3. Справочные материалы",
        "Приложение А3. Справочные материалы",
        "text",
        "Таблица 2. Дозы",
    )
    tables = fragments[1:]
    assert len(tables) > 1
    assert all(f.kind == "table" for f in tables)
    assert all(f.text.startswith("Препарат | Доза, мг/сут\nПрепарат ") for f in tables)
    assert all(len(f.text) <= 2000 for f in tables)
    rows = [line for f in tables for line in f.text.split("\n")[1:]]
    assert rows == [f"Препарат {i} | {i}–{i + 5}" for i in range(150)]


@pytest.mark.parametrize(
    "section_id",
    [
        "doc_whole",
        "doc_title",
        "doc_key_words",
        "doc_abbreviation",
        "doc_bible",
        "doc_a1",
        "doc_a2",
        "doc_g",
    ],
)
def test_service_sections_are_skipped(section_id: str) -> None:
    html = _p("Служебный текст.") + _li("<strong>Рекомендуется</strong> Пи.") + _grade(A1)

    assert parse_guideline(_guideline((section_id, "Служебный раздел", html))) == []


def test_empty_section_content_gives_no_fragments() -> None:
    assert parse_guideline(_guideline(("doc_1", "1. Краткая информация", None))) == []


@pytest.mark.parametrize(
    ("data", "match"),
    [
        pytest.param({"id": "900_1"}, "obj.sections", id="нет разделов"),
        pytest.param(
            {"id": "900_1", "obj": {"sections": [{"id": "doc_3"}]}},
            "obj.sections",
            id="раздел без заголовка",
        ),
        pytest.param(
            _guideline(("doc_3", "3. Лечение", _li(f"Пациентам рекомендуется Ро. {A1}"))),
            "doc_3",
            id="строка уровней внутри абзаца пункта",
        ),
        pytest.param(
            _guideline(
                ("doc_3", "3. Лечение", f"<table><tr><td>Сигма</td><td>{A1}</td></tr></table>")
            ),
            "doc_3",
            id="строка уровней в таблице",
        ),
        pytest.param(
            _guideline(
                (
                    "doc_3",
                    "3. Лечение",
                    _li("Пациентам рекомендуется Тау.")
                    + _grade(A1)
                    + _p("Комментарии: " + "т" * 20_000),
                )
            ),
            "20000",
            id="фрагмент длиннее 20 000 символов",
        ),
    ],
)
def test_parse_errors(data: dict[str, Any], match: str) -> None:
    with pytest.raises(ParseError, match=match):
        parse_guideline(data)
