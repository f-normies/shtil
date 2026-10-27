"""Импорт набора v3 (спека #92, 3.2): цитаты → фрагменты, стиль, вид, деление, без цитат КР."""

from collections import Counter
from typing import Any

import pytest

from dataset import Split, fingerprint
from import_v3 import (
    build_questions,
    copied_text,
    kind_of,
    match_quote,
    replace_questions,
    style_of,
)
from retrieval import Doc

DOSING = (
    "Внутрь по 1 таблетке (20 мг) 2 раза в сутки (утром и вечером). Длительность "
    "курсового применения препарата составляет 4 недели."
)
CONTRA = (
    "Почечная недостаточность средней и тяжелой степени (СКФ <60 мл/мин). "
    "Наблюдение врачом-психиатром."
)
TABLE = "Терапия первой линии: пароксетин** 20-40 мг/сут, #сертралин** 50-200 мг/сут"


def _doc(key: str, source: str, text: str) -> Doc:
    return Doc(key=key, source_id=source, title="T", heading_path="H", text=text)


DOCS = {
    d.key: d
    for d in (
        _doc("aviandr-ohlp/4.2/1", "aviandr-ohlp", DOSING),
        _doc("aviandr-ohlp/4.3/1", "aviandr-ohlp", CONTRA),
        _doc("kr-gad-2024/doc_3/3", "kr-gad-2024", TABLE),
    )
}


def test_quote_matches_regardless_of_case_spaces_dashes_and_yo() -> None:
    assert match_quote("#сертралин**50–200 мг/сут", "kr-gad-2024", DOCS) == ["kr-gad-2024/doc_3/3"]
    assert match_quote(
        "почечная  недостаточность средней и тяжёлой степени", "aviandr-ohlp", DOCS
    ) == ["aviandr-ohlp/4.3/1"]


def test_quote_matches_with_hyphen_dropped() -> None:
    assert match_quote("Наблюдение врачом психиатром", "aviandr-ohlp", DOCS) == [
        "aviandr-ohlp/4.3/1"
    ]


def test_quote_matches_table_row_without_cell_separators() -> None:
    docs = {
        "kr-gad-2024/doc_g/2": _doc(
            "kr-gad-2024/doc_g/2", "kr-gad-2024", "0-4 | Минимальный\n5-9 | Умеренный"
        )
    }

    assert match_quote("0-4 Минимальный 5-9 Умеренный", "kr-gad-2024", docs) == [
        "kr-gad-2024/doc_g/2"
    ]


def test_quote_is_searched_only_in_its_source() -> None:
    assert match_quote("20 мг", "kr-gad-2024", DOCS) == []


@pytest.mark.parametrize(
    ("question", "style"),
    [
        ("авиандр при гтр как пить", "short"),
        ("«авиандр» до еды?", "short"),
        ("Можно ли назначить Авиандр?", "full"),
        ("PHQ-9 где порог", "full"),
    ],
)
def test_style_by_first_letter(question: str, style: str) -> None:
    assert style_of(question) == style


def test_kind_from_evidence_fragments() -> None:
    assert kind_of([]) == "none"
    assert kind_of([["a/1/1"], ["a/1/1"]]) == "single"
    assert kind_of([["a/1/1"], ["a/1/2"]]) == "multi"


def test_copied_text_finds_eight_words_in_a_row() -> None:
    texts = [
        "Принимают утром и вечером. Длительность курсового применения препарата "
        "составляет 4 недели.",
        "Курс при тревоге — четыре недели, при необходимости до шести.",
    ]

    assert copied_text(texts, DOCS) == [texts[0]]


def test_numbers_do_not_count_as_copied_words() -> None:
    docs = {
        "g/1": _doc(
            "g/1", "kr-gad-2024", "0-4 Минимальный 5-9 Умеренный 10-14 Средний 15-21 Высокий"
        )
    }
    texts = ["Пороги: 0–4 — минимальный, 5–9 — умеренный, 10–14 — средний, 15–21 — высокий."]

    assert copied_text(texts, docs) == []


def _point(quote: str, doc: str = "aviandr-ohlp", critical: bool = False) -> dict[str, Any]:
    return {
        "text": "Пересказ пункта своими словами.",
        "critical": critical,
        "evidence": [{"doc": doc, "section": "4.2", "quote": quote, "pdf_pages": [3]}],
    }


def _item(qid: str, qtype: str, points: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    item = {
        "id": qid,
        "author": "врач",
        "asker": "терапевт",
        "type": qtype,
        "patient": None,
        "question": f"вопрос {qid}",
        "points": points,
        "sources": [],
        "must_not": [],
        "comment": "",
    }
    item.update(extra)
    return item


def _files(items: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    plain = [{**i, "points": [], "comment": ""} for i in items]
    return {"questions": plain}, {"questions": items}


def test_build_questions_maps_quotes_to_fragments_without_quotes() -> None:
    items = [
        _item("s-001", "reference", [_point("2 раза в сутки (утром и вечером)")]),
        _item(
            "s-002",
            "case",
            [_point("СКФ <60 мл/мин", critical=True), _point("курсового применения")],
            patient="Ж, 61 год. СКФ 52.",
            must_not=["Назначить стандартную дозу."],
            comment=" Частичный ответ. ",
        ),
        _item("s-003", "none", []),
    ]

    questions, problems = build_questions(*_files(items), DOCS, seed=92)

    assert problems == []
    by_id = {q.id: q for q in questions}
    assert by_id["s-001"].kind == "single"
    assert by_id["s-001"].points[0].evidence[0].fingerprint == fingerprint(DOSING)
    assert by_id["s-002"].kind == "multi"
    assert by_id["s-002"].patient == "Ж, 61 год. СКФ 52."
    assert by_id["s-002"].note == "Частичный ответ."
    assert {r.key for r in by_id["s-002"].relevant} == {"aviandr-ohlp/4.2/1", "aviandr-ohlp/4.3/1"}
    assert by_id["s-003"].kind == "none"
    assert "quote" not in repr(questions)
    assert "утром и вечером" not in repr(questions)


def test_document_names_are_mapped_to_manifest() -> None:
    items = [_item("s-001", "reference", [_point("#сертралин** 50-200 мг/сут", doc="kr-gtr-2024")])]

    questions, problems = build_questions(*_files(items), DOCS, seed=92)

    assert problems == []
    assert questions[0].points[0].evidence[0].key == "kr-gad-2024/doc_3/3"


def test_missing_quote_and_changed_question_are_reported() -> None:
    items = [_item("s-001", "reference", [_point("этой цитаты нет в документе")])]
    plain, annotated = _files(items)
    plain["questions"][0]["question"] = "другой текст"

    questions, problems = build_questions(plain, annotated, DOCS, seed=92)

    assert questions == []
    assert any("s-001" in p and "цитата не найдена" in p for p in problems)
    assert any("s-001" in p and "вопрос изменён" in p for p in problems)


def test_copied_point_is_reported() -> None:
    point = _point("2 раза в сутки")
    point["text"] = (
        "Принимают утром и вечером; длительность курсового применения препарата "
        "составляет 4 недели."
    )
    questions, problems = build_questions(
        *_files([_item("s-001", "reference", [point])]), DOCS, seed=92
    )

    assert questions == []
    assert any("s-001" in p and "дословно" in p for p in problems)


def test_split_is_half_within_each_type() -> None:
    items = [_item(f"s-{n:03}", "none", []) for n in range(10)] + [
        _item(f"s-{n:03}", "reference", [_point("2 раза в сутки")]) for n in range(10, 14)
    ]

    questions, problems = build_questions(*_files(items), DOCS, seed=92)

    assert problems == []
    assert Counter((q.type, q.split) for q in questions) == {
        ("none", "dev"): 5,
        ("none", "test"): 5,
        ("reference", "dev"): 2,
        ("reference", "test"): 2,
    }


def test_known_split_is_kept_after_reannotation() -> None:
    # Переразметка меняет вид вопроса (спека #105, 3.3); деление остальных вопросов не сдвигается.
    items = [_item(f"s-{n:03}", "reference", [_point("2 раза в сутки")]) for n in range(4)]
    known: dict[str, Split] = {"s-000": "test", "s-001": "test", "s-002": "test"}

    questions, problems = build_questions(*_files(items), DOCS, seed=92, splits=known)

    assert problems == []
    assert {q.id: q.split for q in questions if q.id in known} == known
    assert {q.id for q in questions} == {"s-000", "s-001", "s-002", "s-003"}


def test_reannotated_questions_replace_their_old_versions() -> None:
    items = [_item(f"s-{n:03}", "reference", [_point("2 раза в сутки")]) for n in range(3)]
    old, _ = build_questions(*_files(items), DOCS, seed=92)
    again = [_item("s-001", "none", [], comment="Ответа нет.")]
    new, _ = build_questions(*_files(again), DOCS, seed=92, splits={q.id: q.split for q in old})

    merged = replace_questions(old, new)

    assert [q.id for q in merged] == ["s-000", "s-001", "s-002"]
    assert merged[1].type == "none"
    assert merged[1].split == old[1].split
    assert merged[0] == old[0]


def test_reannotated_question_must_exist() -> None:
    items = [_item("s-001", "reference", [_point("2 раза в сутки")])]
    old, _ = build_questions(*_files(items), DOCS, seed=92)
    new, _ = build_questions(*_files([_item("s-009", "none", [])]), DOCS, seed=92)

    with pytest.raises(ValueError, match="s-009"):
        replace_questions(old, new)
