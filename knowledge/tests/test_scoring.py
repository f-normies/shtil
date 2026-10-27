"""Подсчёт балла шкалы (спека, раздел 6). Шкала — выдуманный пример из fixtures."""

import pytest

from clin_compass_knowledge.loader import load_knowledge
from clin_compass_knowledge.schemas.scale import Scale
from clin_compass_knowledge.scoring import ScoringError, score
from tests.helpers import VALID


@pytest.fixture(scope="module")
def scale() -> Scale:
    """Шкала `example-mood` из хорошего набора примеров."""
    return load_knowledge(VALID).scales["example-mood"]


@pytest.mark.parametrize(
    ("answers", "total", "band"),
    [
        ({"q1": 0, "q2": 0, "q3": 0}, 0, "minimal"),
        ({"q1": 1, "q2": 1, "q3": 1}, 3, "minimal"),  # верхняя граница порога
        ({"q1": 2, "q2": 2, "q3": 0}, 4, "moderate"),  # нижняя граница следующего
        ({"q1": 3, "q2": 3, "q3": 0}, 6, "moderate"),
        ({"q1": 3, "q2": 3, "q3": 1}, 7, "severe"),
        ({"q1": 3, "q2": 3, "q3": 3}, 9, "severe"),
    ],
)
def test_total_and_band(scale: Scale, answers: dict[str, int], total: int, band: str) -> None:
    result = score(scale, answers)["total"]

    assert result.value == total
    assert result.band.code == band


@pytest.mark.parametrize(
    ("answers", "total", "band"),
    [
        ({"q1": 1, "q2": 1, "q3": 3}, 2, "below"),  # q3 во вторую сумму не входит
        ({"q1": 2, "q2": 1, "q3": 0}, 3, "above"),
    ],
)
def test_second_score_sums_its_own_items(
    scale: Scale, answers: dict[str, int], total: int, band: str
) -> None:
    result = score(scale, answers)["core"]

    assert result.value == total
    assert result.band.code == band


def test_missing_answer_is_an_error(scale: Scale) -> None:
    with pytest.raises(ScoringError, match="нет ответа на пункт q3"):
        score(scale, {"q1": 0, "q2": 0})


def test_value_not_from_options_is_an_error(scale: Scale) -> None:
    with pytest.raises(ScoringError, match="ответ 5 на пункт q1 не из вариантов"):
        score(scale, {"q1": 5, "q2": 0, "q3": 0})


def test_total_outside_bands_is_an_error(scale: Scale) -> None:
    """Шкала с пропуском в порогах: подсчёт сообщает об этом через ScoringError."""
    bands = scale.scores[0].bands
    broken_score = scale.scores[0].model_copy(update={"bands": bands[:2]})  # без 7–9
    broken = scale.model_copy(update={"scores": (broken_score,)})

    with pytest.raises(ScoringError, match="сумма total = 9 не попадает ни в один порог"):
        score(broken, {"q1": 3, "q2": 3, "q3": 3})


def test_unknown_item_is_an_error(scale: Scale) -> None:
    with pytest.raises(ScoringError, match="нет пункта q9"):
        score(scale, {"q1": 0, "q2": 0, "q3": 0, "q9": 1})
