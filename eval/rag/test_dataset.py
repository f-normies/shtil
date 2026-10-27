"""Набор вопросов v2 (спека #70, раздел 3): формат, деление, фильтр и правила отбора."""

import hashlib
from collections import Counter
from pathlib import Path

import pytest

from dataset import (
    Question,
    check_index,
    dump_questions,
    fingerprint,
    keep_after_labeling,
    load_questions,
    relevant_keys,
    shares_ngram,
    split_ids,
)


def _question(**overrides: object) -> dict[str, object]:
    raw: dict[str, object] = {
        "id": "q-001",
        "question": "Когда назначать ЭЭГ при ГТР?",
        "kind": "single",
        "split": "dev",
        "origin": ["kr-gad-2024/doc_diag_2_4/2"],
        "relevant": [
            {"key": "kr-gad-2024/doc_diag_2_4/2", "fingerprint": "0123456789ab", "grade": 2},
            {"key": "kr-gad-2024/doc_diag_2_4/1", "fingerprint": "ba9876543210", "grade": 1},
        ],
    }
    raw.update(overrides)
    return raw


def test_fingerprint_is_12_hex_of_sha256() -> None:
    assert fingerprint("текст") == hashlib.sha256("текст".encode()).hexdigest()[:12]


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"relevant": []}, "оценкой 2"),
        (
            {"relevant": [{"key": "a/1/1", "fingerprint": "0123456789ab", "grade": 1}]},
            "оценкой 2",
        ),
        (
            {
                "kind": "none",
                "origin": [],
                "relevant": [{"key": "a/1/1", "fingerprint": "0123456789ab", "grade": 2}],
            },
            "kind: none",
        ),
        ({"kind": "none"}, "origin"),
        ({"split": "train"}, "split"),
        ({"source_text": "…"}, "source_text"),
        ({"relevant": [{"key": "a/1/1", "fingerprint": "0123456789ab", "grade": 3}]}, "grade"),
    ],
)
def test_question_is_validated(overrides: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        Question.model_validate(_question(**overrides))


def test_multi_question_counts_partial_fragments_as_relevant() -> None:
    multi = Question.model_validate(
        _question(
            kind="multi",
            origin=["a/1/1", "a/1/2"],
            relevant=[
                {"key": "a/1/1", "fingerprint": "0123456789ab", "grade": 1},
                {"key": "a/1/2", "fingerprint": "0123456789ab", "grade": 1},
            ],
        )
    )
    single = Question.model_validate(_question())

    assert relevant_keys(multi) == {"a/1/1", "a/1/2"}
    assert relevant_keys(single) == {"kr-gad-2024/doc_diag_2_4/2"}


def test_questions_round_trip_through_yaml(tmp_path: Path) -> None:
    path = tmp_path / "questions.yaml"
    questions = [Question.model_validate(_question())]

    dump_questions(questions, path, header="# Набор вопросов\n")

    assert path.read_text(encoding="utf-8").startswith("# Набор вопросов\n")
    assert load_questions(path) == questions


def test_repeated_question_id_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "questions.yaml"
    dump_questions([Question.model_validate(_question())] * 2, path, header="")

    with pytest.raises(ValueError, match="q-001"):
        load_questions(path)


def test_question_set_loads() -> None:
    load_questions()


def test_check_index_reports_missing_and_changed_fragments() -> None:
    question = Question.model_validate(_question())
    index = {"kr-gad-2024/doc_diag_2_4/2": "ffffffffffff"}

    problems = check_index([question], index)

    assert problems == [
        "q-001: kr-gad-2024/doc_diag_2_4/2 изменился: "
        "в наборе 0123456789ab, в индексе ffffffffffff",
        "q-001: kr-gad-2024/doc_diag_2_4/1 нет в действующем индексе",
    ]


def test_split_is_stratified_and_reproducible() -> None:
    items = [(f"a-{i}", "kr-gad/single") for i in range(14)] + [
        (f"b-{i}", "ohlp/none") for i in range(7)
    ]

    first = split_ids(items, test_share=2 / 7, seed=70)
    second = split_ids(list(reversed(items)), test_share=2 / 7, seed=70)

    assert first == second
    by_stratum = Counter((key[0], split) for key, split in first.items())
    assert by_stratum == {("a", "test"): 4, ("a", "dev"): 10, ("b", "test"): 2, ("b", "dev"): 5}


@pytest.mark.parametrize(
    ("question", "shares"),
    [
        ("Проводить ли ЭЭГ, если нельзя исключить пароксизмальные состояния?", False),
        (
            "Если не представляется возможным с высокой степенью надёжности исключить приступы?",
            True,
        ),
    ],
)
def test_shares_ngram_finds_five_words_in_a_row(question: str, shares: bool) -> None:
    text = (
        "Пациентам, у которых не представляется возможным с высокой степенью надежности "
        "исключить наличие пароксизмальных состояний, рекомендуется ЭЭГ."
    )

    assert shares_ngram(question, text, n=5) is shares


@pytest.mark.parametrize(
    ("kind", "origin", "grades", "keep"),
    [
        ("single", ["a"], {"a": 2}, True),
        ("single", ["a"], {"a": 1, "b": 2}, False),
        ("multi", ["a", "b"], {"a": 1, "b": 2}, True),
        ("multi", ["a", "b"], {"a": 2}, False),
        ("none", [], {"a": 1}, True),
        ("none", [], {"a": 2}, False),
    ],
)
def test_keep_after_labeling(
    kind: str, origin: list[str], grades: dict[str, int], keep: bool
) -> None:
    assert keep_after_labeling(kind, origin, grades) is keep
