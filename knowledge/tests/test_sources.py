"""Источники манифеста: коды МКБ-10 клинических рекомендаций (спека #107, 4.1)."""

from pathlib import Path
from typing import Any

import pytest

from clin_compass_knowledge.loader import load_knowledge
from tests.helpers import VALID, Edit, messages

MANIFEST = "sources/manifest.yaml"
# Индексы записей в fixtures/valid/sources/manifest.yaml.
GUIDELINE = 0
LABEL = 3


def test_guideline_lists_covered_codes() -> None:
    source = load_knowledge(VALID).sources["example-guideline"]

    assert source.icd10 == ("F32", "F33")


def test_other_sources_have_no_codes() -> None:
    sources = load_knowledge(VALID).sources

    assert sources["example-label"].icd10 == ()


def set_codes(index: int, codes: list[str]) -> Any:
    def change(data: Any) -> None:
        data["sources"][index]["icd10"] = codes

    return change


def drop_codes(data: Any) -> None:
    data["sources"][GUIDELINE].pop("icd10")


@pytest.mark.parametrize(
    ("change", "path"),
    [
        # КР без кодов не найдётся ни по одному диагнозу пациента.
        (drop_codes, f"sources[{GUIDELINE}]"),
        (set_codes(GUIDELINE, []), f"sources[{GUIDELINE}]"),
        # Коды МКБ-10 у инструкции не задаются: её показания — в карточке препарата.
        (set_codes(LABEL, ["F41.1"]), f"sources[{LABEL}]"),
        (set_codes(GUIDELINE, ["F41,1"]), f"sources[{GUIDELINE}].icd10[0]"),
    ],
)
def test_codes_are_checked(tree: Path, edit: Edit, change: Any, path: str) -> None:
    edit(MANIFEST, change)

    found = messages(tree)

    assert any(m.startswith(f"{MANIFEST}: {path}:") for m in found), found
