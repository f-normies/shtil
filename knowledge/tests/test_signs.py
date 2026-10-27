"""Виды признаков риска и реестр препаратов в манифесте (спека #32, разделы 5.2 и 6.1)."""

from pathlib import Path

from clin_compass_knowledge.loader import load_knowledge
from tests.helpers import VALID, Edit, messages

INTENT = "signs/example-intent.yaml"
THOUGHTS = "signs/example-thoughts.yaml"
MANIFEST = "sources/manifest.yaml"


def test_signs_are_loaded_by_id() -> None:
    knowledge = load_knowledge(VALID)

    assert set(knowledge.signs) == {"example-intent", "example-thoughts"}
    assert knowledge.signs["example-intent"].rank == 1


def test_sign_needs_source(tree: Path, edit: Edit) -> None:
    edit(INTENT, lambda d: d.update(sources=[]))

    assert messages(tree) == [f"{INTENT}: sources: минимум элементов в списке — 1, сейчас 0"]


def test_sign_source_must_be_in_manifest(tree: Path, edit: Edit) -> None:
    edit(INTENT, lambda d: d["sources"][0].update(source="nope"))

    assert messages(tree) == [f"{INTENT}: sources[0].source: нет источника nope в манифесте"]


def test_sign_with_only_supplementary_sources(tree: Path, edit: Edit) -> None:
    edit(INTENT, lambda d: d["sources"][0].update(source="example-international"))

    assert messages(tree) == [f"{INTENT}: sources: только дополнительные источники: нужен основной"]


def test_sign_needs_doctor_action(tree: Path, edit: Edit) -> None:
    edit(INTENT, lambda d: d.update(doctor_actions=[]))

    assert messages(tree) == [f"{INTENT}: doctor_actions: минимум элементов в списке — 1, сейчас 0"]


def test_sign_extra_field_is_reported(tree: Path, edit: Edit) -> None:
    edit(INTENT, lambda d: d.update(level="mandatory"))

    assert messages(tree) == [f"{INTENT}: level: лишнее поле"]


def test_reviewed_signs_need_distinct_ranks(tree: Path, edit: Edit) -> None:
    # По рангу выбирается заголовок сигнала: при равных рангах выбор был бы случайным.
    edit(THOUGHTS, lambda d: d.update(rank=1))

    assert messages(tree) == [f"{THOUGHTS}: rank: ранг 1 уже у example-intent"]


def test_draft_sign_may_share_rank(tree: Path, edit: Edit) -> None:
    edit(THOUGHTS, lambda d: d.update(rank=1, status="draft"))

    assert messages(tree) == []


def test_sign_id_must_match_file_name(tree: Path, edit: Edit) -> None:
    edit(INTENT, lambda d: d.update(id="other"))

    assert messages(tree) == [f"{INTENT}: id: не совпадает с именем файла"]


def test_drug_registry_is_a_source_kind(tree: Path, edit: Edit) -> None:
    (tree / "sources" / "files" / "grls.csv").write_text(
        "reg_number,trade_name,inn,status\n", encoding="utf-8"
    )
    registry = {
        "id": "grls",
        "title": "Государственный реестр лекарственных средств",
        "kind": "drug_registry",
        "publisher": "Минздрав России",
        "url": "https://grls.rosminzdrav.ru/GRLS.aspx",
        "version": "2026-10-16",
        "date": "2026-10-16",
        "license": "официальная публикация",
        "file": "files/grls.csv",
        "supplementary": False,
    }
    edit(MANIFEST, lambda d: d["sources"].append(registry))

    assert messages(tree) == []
