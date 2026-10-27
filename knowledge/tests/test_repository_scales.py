"""Пороги настоящих шкал knowledge/scales: граница каждого порога, у случая — источник."""

from pathlib import Path

import pytest

from clin_compass_knowledge.loader import load_knowledge
from clin_compass_knowledge.scoring import score

KNOWLEDGE = load_knowledge(Path(__file__).parents[1])

GAD7_KEY = "zolotareva-gad7-2023, с. 39; 0–4 — kr-gad-2024, прил. Г2"
PHQ9_KEY = "zolotareva-gad7-2023, с. 35"
HAMD_KEY = "kr-depression-2024, прил. Г1, ключ"
HARS_KEY = "kr-gad-2024, прил. Г1, ключ"

CASES = [
    ("gad-7", 0, "minimal", GAD7_KEY),
    ("gad-7", 4, "minimal", GAD7_KEY),
    ("gad-7", 5, "mild", GAD7_KEY),
    ("gad-7", 9, "mild", GAD7_KEY),
    ("gad-7", 10, "moderate", GAD7_KEY),
    ("gad-7", 14, "moderate", GAD7_KEY),
    ("gad-7", 15, "severe", GAD7_KEY),
    ("gad-7", 21, "severe", GAD7_KEY),
    ("phq-9", 0, "minimal", PHQ9_KEY),
    ("phq-9", 4, "minimal", PHQ9_KEY),
    ("phq-9", 5, "mild", PHQ9_KEY),
    ("phq-9", 9, "mild", PHQ9_KEY),
    ("phq-9", 10, "moderate", PHQ9_KEY),
    ("phq-9", 14, "moderate", PHQ9_KEY),
    ("phq-9", 15, "moderately-severe", PHQ9_KEY),
    ("phq-9", 19, "moderately-severe", PHQ9_KEY),
    ("phq-9", 20, "severe", PHQ9_KEY),
    ("phq-9", 27, "severe", PHQ9_KEY),
    ("hamd-17", 0, "normal", HAMD_KEY),
    ("hamd-17", 7, "normal", HAMD_KEY),
    ("hamd-17", 8, "mild", HAMD_KEY),
    ("hamd-17", 13, "mild", HAMD_KEY),
    ("hamd-17", 14, "moderate", HAMD_KEY),
    ("hamd-17", 18, "moderate", HAMD_KEY),
    ("hamd-17", 19, "severe", HAMD_KEY),
    ("hamd-17", 22, "severe", HAMD_KEY),
    ("hamd-17", 23, "extremely-severe", f"{HAMD_KEY}; 23 — решение медика, в КР «более 23»"),
    ("hamd-17", 52, "extremely-severe", HAMD_KEY),
    ("hars", 0, "absent", HARS_KEY),
    ("hars", 7, "absent", HARS_KEY),
    ("hars", 8, "mild", HARS_KEY),
    ("hars", 17, "mild", HARS_KEY),
    ("hars", 18, "moderate", HARS_KEY),
    ("hars", 24, "moderate", HARS_KEY),
    ("hars", 25, "severe", HARS_KEY),
    ("hars", 56, "severe", HARS_KEY),
]


def answers_with_total(scale_id: str, total: int) -> dict[str, int]:
    """Ответы с заданной суммой: пункты по порядку заполняются своим максимальным баллом."""
    result = {}
    for item in KNOWLEDGE.scales[scale_id].items:
        result[item.id] = min(max(o.value for o in item.options), total)
        total -= result[item.id]
    return result


@pytest.mark.parametrize(("scale_id", "total", "band", "source"), CASES)
def test_band_boundaries(scale_id: str, total: int, band: str, source: str) -> None:
    result = score(KNOWLEDGE.scales[scale_id], answers_with_total(scale_id, total))["total"]

    assert (result.value, result.band.code) == (total, band), source
