"""Набор вопросов v3 (спека #92, раздел 3): формат, проверки и загрузка."""

from pathlib import Path

import pytest

from dataset import relevant_keys
from dataset_v3 import QuestionV3, dump_questions_v3, evidence_keys, load_questions_v3

KEY = "aviandr-ohlp/4.3/1"
FP = "0123456789ab"


def _raw(**overrides: object) -> dict[str, object]:
    raw: dict[str, object] = {
        "id": "s-048",
        "question": "авиандр ей можно или почки уже не позволяют",
        "patient": "Ж, 61 год. ХБП, СКФ 52 мл/мин.",
        "type": "case",
        "asker": "терапевт",
        "style": "short",
        "kind": "single",
        "split": "test",
        "points": [
            {
                "text": "При СКФ < 60 мл/мин Авиандр противопоказан.",
                "critical": True,
                "evidence": [{"key": KEY, "fingerprint": FP}],
            }
        ],
        "must_not": ["Назначить 20 мг 2 раза в сутки."],
        "relevant": [{"key": KEY, "fingerprint": FP, "grade": 2}],
    }
    raw.update(overrides)
    return raw


def test_valid_question_loads() -> None:
    question = QuestionV3.model_validate(_raw())

    assert evidence_keys(question) == {KEY}
    assert relevant_keys(question) == {KEY}


def test_question_without_patient_and_note_is_valid() -> None:
    raw = _raw(type="reference")
    del raw["patient"]

    assert QuestionV3.model_validate(raw).patient is None


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"type": "faq"}, "type"),
        ({"style": "long"}, "style"),
        ({"kind": "none"}, "kind: none"),
        ({"points": [], "kind": "single"}, "пункт"),
        ({"relevant": []}, "оценкой 2"),
        ({"relevant": [{"key": KEY, "fingerprint": FP, "grade": 1}]}, "оценкой 2"),
        (
            {
                "points": [],
                "kind": "none",
                "relevant": [{"key": "a/1/1", "fingerprint": FP, "grade": 2}],
            },
            "kind: none",
        ),
        (
            {
                "kind": "multi",
            },
            "multi",
        ),
        ({"quote": "дословно"}, "quote"),
    ],
)
def test_question_is_validated(overrides: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        QuestionV3.model_validate(_raw(**overrides))


def test_multi_needs_two_evidence_fragments() -> None:
    second = "aviandr-ohlp/4.2/1"
    raw = _raw(
        kind="multi",
        points=[
            {
                "text": "Противопоказан при СКФ < 60.",
                "critical": True,
                "evidence": [{"key": KEY, "fingerprint": FP}],
            },
            {
                "text": "Доза 20 мг 2 раза в сутки.",
                "critical": False,
                "evidence": [{"key": second, "fingerprint": FP}],
            },
        ],
        relevant=[
            {"key": KEY, "fingerprint": FP, "grade": 2},
            {"key": second, "fingerprint": FP, "grade": 2},
        ],
    )

    assert QuestionV3.model_validate(raw).kind == "multi"


def test_none_question_has_no_points() -> None:
    question = QuestionV3.model_validate(
        _raw(type="none", kind="none", points=[], relevant=[], must_not=[])
    )

    assert evidence_keys(question) == set()


def test_questions_round_trip_through_yaml(tmp_path: Path) -> None:
    path = tmp_path / "questions_v3.yaml"
    questions = [QuestionV3.model_validate(_raw())]

    dump_questions_v3(questions, path, header="# Набор v3\n")

    assert path.read_text(encoding="utf-8").startswith("# Набор v3\n")
    assert load_questions_v3(path) == questions


def test_repeated_id_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "questions_v3.yaml"
    dump_questions_v3([QuestionV3.model_validate(_raw())] * 2, path, header="")

    with pytest.raises(ValueError, match="s-048"):
        load_questions_v3(path)
