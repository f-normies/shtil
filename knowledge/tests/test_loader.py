"""Загрузка каталога и проверки схем (спека, разделы 4 и 5)."""

import json
from pathlib import Path

import pytest

from clin_compass_knowledge.loader import KnowledgeError, load_knowledge
from tests.helpers import VALID, Edit, messages

SCALE = "scales/example-mood.yaml"
MANIFEST = "sources/manifest.yaml"


def test_valid_examples_load_without_issues() -> None:
    knowledge = load_knowledge(VALID)

    assert set(knowledge.sources) == {
        "example-guideline",
        "example-validation",
        "example-international",
        "example-label",
        "example-leaflet",
        "example-registry",
    }
    assert set(knowledge.scales) == {"example-mood"}
    assert set(knowledge.rules) == {"example-self-harm", "example-severe-routing"}
    assert set(knowledge.scenarios) == {"example-scenario"}
    assert set(knowledge.cases) == {"case-01"}
    expected = knowledge.cases["case-01"].expected
    assert expected.diagnoses[0].icd10 == "F32.1"
    assert expected.medications[0].status == "current"
    assert expected.anamnesis[0].fact == "сниженное настроение около месяца"
    assert expected.anamnesis[0].quote == "сниженное настроение около месяца"


def test_directory_with_empty_manifest_is_valid(tmp_path: Path) -> None:
    (tmp_path / "sources").mkdir()
    (tmp_path / MANIFEST).write_text("sources: []\n", encoding="utf-8")

    knowledge = load_knowledge(tmp_path)

    assert knowledge.sources == {}
    assert knowledge.scales == {}


def test_load_raises_with_all_issues(tree: Path, edit: Edit) -> None:
    edit("scales/example-mood.yaml", lambda d: d.update(extra_field=1))
    edit("rules/example-self-harm.yaml", lambda d: d.pop("title"))

    with pytest.raises(KnowledgeError) as exc:
        load_knowledge(tree)

    assert len(exc.value.issues) == 2


def test_unknown_field_is_reported_with_file_and_path(tree: Path, edit: Edit) -> None:
    edit("scales/example-mood.yaml", lambda d: d["items"][0].update(txt="опечатка"))

    assert messages(tree) == [
        "scales/example-mood.yaml: items[0].txt: лишнее поле",
    ]


def test_missing_field_is_reported(tree: Path, edit: Edit) -> None:
    edit("rules/example-self-harm.yaml", lambda d: d.pop("title"))

    assert messages(tree) == ["rules/example-self-harm.yaml: title: обязательное поле"]


def test_id_must_be_lowercase_slug(tree: Path, edit: Edit) -> None:
    edit("sources/manifest.yaml", lambda d: d["sources"][1].update(id="Bad_ID"))

    assert messages(tree) == [
        f"{MANIFEST}: sources[1].id: не подходит под формат ^[a-z0-9]+(-[a-z0-9]+)*$"
    ]


def test_number_in_string_field_is_explained(tree: Path, edit: Edit) -> None:
    edit(MANIFEST, lambda d: d["sources"][0].update(version=1))

    assert messages(tree) == [
        f"{MANIFEST}: sources[0].version: нужна строка; число или дату возьмите в кавычки"
    ]


def test_literal_error_lists_values_in_russian(tree: Path, edit: Edit) -> None:
    edit("rules/example-self-harm.yaml", lambda d: d.update(status="final"))

    assert messages(tree) == [
        "rules/example-self-harm.yaml: status: допустимые значения: 'draft' или 'reviewed'"
    ]


def test_validator_message_has_no_english_prefix(tree: Path, edit: Edit) -> None:
    edit("rules/example-self-harm.yaml", lambda d: d["when"]["any"][0].pop("op"))

    assert messages(tree) == [
        "rules/example-self-harm.yaml: when.any[0]: у сравнения нужны scale, op и value"
    ]


def test_icd10_code_format_is_checked(tree: Path) -> None:
    path = tree / "cases/case-01/expected.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["diagnoses"][0]["icd10"] = "F32-1"
    path.write_text(json.dumps(data), encoding="utf-8")

    assert [m.split(": ")[1] for m in messages(tree)] == ["diagnoses[0].icd10"]


def test_medication_dose_and_regimen_may_be_absent(tree: Path) -> None:
    path = tree / "cases/case-01/expected.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["medications"][0].update(dose=None, regimen=None)
    path.write_text(json.dumps(data), encoding="utf-8")

    assert messages(tree) == []


@pytest.mark.parametrize("field", ["risk_factors", "anamnesis"])
def test_case_fact_needs_quote(tree: Path, field: str) -> None:
    path = tree / "cases/case-01/expected.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data[field] = [{"fact": "сниженное настроение около месяца"}]
    path.write_text(json.dumps(data), encoding="utf-8")

    assert messages(tree) == [f"cases/case-01/expected.json: {field}[0].quote: обязательное поле"]


def test_medication_status_is_checked(tree: Path) -> None:
    path = tree / "cases/case-01/expected.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["medications"][0]["status"] = "planned"
    path.write_text(json.dumps(data), encoding="utf-8")

    assert messages(tree) == [
        "cases/case-01/expected.json: medications[0].status: "
        "допустимые значения: 'current' или 'past'"
    ]


def test_broken_yaml_is_reported(tree: Path) -> None:
    (tree / "rules/example-self-harm.yaml").write_text("id: [", encoding="utf-8")

    issues = messages(tree)

    assert len(issues) == 1
    assert issues[0].startswith("rules/example-self-harm.yaml: не читается YAML")


def test_case_without_expected_is_reported(tree: Path) -> None:
    (tree / "cases/case-01/expected.json").unlink()

    assert messages(tree) == ["cases/case-01/expected.json: файла нет"]


def test_missing_manifest_is_reported(tmp_path: Path) -> None:
    assert messages(tmp_path) == [f"{MANIFEST}: файла нет"]


def test_empty_file_is_reported(tree: Path) -> None:
    (tree / SCALE).write_text("", encoding="utf-8")

    assert messages(tree) == [f"{SCALE}: файл пустой"]


def test_comments_only_manifest_is_reported(tree: Path) -> None:
    (tree / MANIFEST).write_text("# только комментарий\n", encoding="utf-8")

    assert messages(tree) == [f"{MANIFEST}: файл пустой"]


def test_null_json_is_reported(tree: Path) -> None:
    (tree / "cases/case-01/expected.json").write_text("null", encoding="utf-8")

    assert messages(tree) == ["cases/case-01/expected.json: файл пустой"]


def test_non_utf8_yaml_is_reported(tree: Path) -> None:
    (tree / SCALE).write_bytes("title: Пример\n".encode("cp1251"))

    assert messages(tree) == [f"{SCALE}: файл не в кодировке UTF-8"]


def test_non_utf8_history_is_reported(tree: Path) -> None:
    (tree / "cases/case-01/history.md").write_bytes("Выписка".encode("cp1251"))

    assert messages(tree) == ["cases/case-01/history.md: файл не в кодировке UTF-8"]


def test_duplicate_yaml_key_is_reported(tree: Path) -> None:
    path = tree / SCALE
    path.write_text(path.read_text(encoding="utf-8") + "title: другое\n", encoding="utf-8")

    issues = messages(tree)

    assert len(issues) == 1
    assert issues[0].startswith(f"{SCALE}: не читается YAML: повторяется ключ title")


def test_non_yaml_file_in_folder_is_reported(tree: Path) -> None:
    (tree / "rules/notes.txt").write_text("заметка", encoding="utf-8")
    (tree / "rules/.DS_Store").write_bytes(b"")  # скрытые файлы не проверяются

    assert messages(tree) == ["rules/notes.txt: не загружается: нужен файл <id>.yaml"]


def test_file_directly_in_cases_is_reported(tree: Path) -> None:
    (tree / "cases/history.md").write_text("Выписка", encoding="utf-8")

    assert messages(tree) == [
        "cases/history.md: не загружается: история лежит в каталоге cases/<id>/"
    ]


def test_id_not_matching_file_name_does_not_cascade(tree: Path, edit: Edit) -> None:
    edit(SCALE, lambda d: d.update(id="example-other"))

    assert messages(tree) == [f"{SCALE}: id: не совпадает с именем файла"]


def test_broken_scale_does_not_cascade_into_rules(tree: Path, edit: Edit) -> None:
    edit(SCALE, lambda d: d.pop("title"))

    assert messages(tree) == [f"{SCALE}: title: обязательное поле"]
