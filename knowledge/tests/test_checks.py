"""Перекрёстные проверки (спека, раздел 5): на каждую — плохой пример из хорошего набора."""

import json
from pathlib import Path
from typing import Any

import pytest

from tests.helpers import VALID, Edit, messages

SCALE = "scales/example-mood.yaml"
ALERT = "rules/example-self-harm.yaml"
ROUTING = "rules/example-severe-routing.yaml"
SCENARIO = "scenarios/example-scenario.yaml"
MANIFEST = "sources/manifest.yaml"
CASE = "cases/case-01/expected.json"


def test_valid_examples_pass_all_checks() -> None:
    assert messages(VALID) == []


# --- источники ---------------------------------------------------------------


def test_duplicate_source_id(tree: Path, edit: Edit) -> None:
    edit(MANIFEST, lambda d: d["sources"][2].update(id="example-guideline"))

    assert messages(tree) == [f"{MANIFEST}: sources[2].id: повторяется example-guideline"]


def test_source_copy_file_must_exist(tree: Path, edit: Edit) -> None:
    edit(MANIFEST, lambda d: d["sources"][0].update(file="files/missing.pdf"))

    assert messages(tree) == [f"{MANIFEST}: sources[0].file: нет файла sources/files/missing.pdf"]


@pytest.mark.parametrize("file", ["manifest.yaml", "files/../manifest.yaml", "/etc/hosts"])
def test_source_copy_must_lie_in_files(tree: Path, edit: Edit, file: str) -> None:
    edit(MANIFEST, lambda d: d["sources"][0].update(file=file))

    assert messages(tree) == [
        f"{MANIFEST}: sources[0].file: копия лежит в sources/files/: путь вида files/<имя>"
    ]


def test_supplementary_only_for_other_kind(tree: Path, edit: Edit) -> None:
    edit(MANIFEST, lambda d: d["sources"][0].update(supplementary=True))

    assert messages(tree) == [
        f"{MANIFEST}: sources[0].supplementary: дополнением может быть только источник kind: other"
    ]


def test_unknown_source_reference(tree: Path, edit: Edit) -> None:
    edit(SCALE, lambda d: d["validation"][0].update(source="nope"))

    assert messages(tree) == [f"{SCALE}: validation[0].source: нет источника nope в манифесте"]


def test_unknown_source_reference_in_rule_case(tree: Path, edit: Edit) -> None:
    edit(ALERT, lambda d: d["cases"][1]["source"].update(source="nope"))

    assert messages(tree) == [f"{ALERT}: cases[1].source.source: нет источника nope в манифесте"]


def test_rule_with_only_supplementary_sources(tree: Path, edit: Edit) -> None:
    edit(ALERT, lambda d: d.update(sources=[{"source": "example-international", "locator": "1"}]))

    assert messages(tree) == [f"{ALERT}: sources: только дополнительные источники: нужен основной"]


def test_supplementary_source_next_to_primary_is_fine(tree: Path, edit: Edit) -> None:
    edit(ALERT, lambda d: d["sources"].append({"source": "example-international", "locator": "1"}))

    assert messages(tree) == []


def test_band_with_only_supplementary_source(tree: Path, edit: Edit) -> None:
    edit(
        SCALE, lambda d: d["scores"][0]["bands"][0]["source"].update(source="example-international")
    )

    assert messages(tree) == [
        f"{SCALE}: scores[0].bands[0].source: только дополнительные источники: нужен основной"
    ]


def test_scale_validation_with_only_supplementary_sources(tree: Path, edit: Edit) -> None:
    edit(SCALE, lambda d: d["validation"][0].update(source="example-international"))

    assert messages(tree) == [
        f"{SCALE}: validation: только дополнительные источники: нужен основной"
    ]


def test_scale_validation_needs_validation_publication(tree: Path, edit: Edit) -> None:
    edit(SCALE, lambda d: d["validation"][0].update(source="example-guideline"))

    assert messages(tree) == [
        f"{SCALE}: validation: нужна ссылка на публикацию о валидации (kind: scale_validation)"
    ]


def test_clinician_scale_validation_accepts_guideline(tree: Path, edit: Edit) -> None:
    def change(d: dict[str, Any]) -> None:
        d["respondent"] = "clinician"
        d["validation"][0]["source"] = "example-guideline"

    edit(SCALE, change)
    edit("scale-schedule.yaml", lambda d: d.update(scales=[]))  # врача не опрашивают по расписанию

    assert messages(tree) == []


def test_clinician_scale_validation_needs_validation_or_guideline(tree: Path, edit: Edit) -> None:
    label = {
        "id": "example-scale-label",
        "title": "Пример инструкции по применению",
        "kind": "drug_label",
        "publisher": "Пример держателя",
        "url": "https://example.org/label",
        "version": "1",
        "date": "2024-02-01",
        "license": "только ссылка",
        "file": None,
        "supplementary": False,
    }
    edit(MANIFEST, lambda d: d["sources"].append(label))

    def change(d: dict[str, Any]) -> None:
        d["respondent"] = "clinician"
        d["validation"][0]["source"] = "example-scale-label"

    edit(SCALE, change)
    edit("scale-schedule.yaml", lambda d: d.update(scales=[]))  # врача не опрашивают по расписанию

    assert messages(tree) == [
        f"{SCALE}: validation: нужна ссылка на публикацию о валидации (kind: scale_validation)"
        " или на приложение КР с русским текстом шкалы (kind: clinical_guideline)"
    ]


# --- шкалы ---------------------------------------------------------------


def test_disallowed_license_requires_draft(tree: Path, edit: Edit) -> None:
    edit(SCALE, lambda d: d["license"].update(allowed=False))

    assert messages(tree) == [
        f"{SCALE}: license.allowed: шкалу без лицензии можно хранить только со status: draft"
    ]


def test_disallowed_license_in_draft_is_fine(tree: Path, edit: Edit) -> None:
    def change(d: dict[str, Any]) -> None:
        d["license"]["allowed"] = False
        d["status"] = "draft"

    edit(SCALE, change)

    assert messages(tree) == []


def test_score_item_must_exist(tree: Path, edit: Edit) -> None:
    edit(SCALE, lambda d: d["scores"][0]["items"].append("q9"))

    assert messages(tree) == [f"{SCALE}: scores[0].items[3]: нет пункта q9"]


def test_bands_must_not_have_gaps(tree: Path, edit: Edit) -> None:
    edit(SCALE, lambda d: d["scores"][0]["bands"][1].update(min=5))

    assert messages(tree) == [f"{SCALE}: scores[0].bands: пропущены баллы 4"]


def test_bands_must_not_overlap(tree: Path, edit: Edit) -> None:
    edit(SCALE, lambda d: d["scores"][0]["bands"][1].update(min=3))

    assert messages(tree) == [f"{SCALE}: scores[0].bands: пороги пересекаются на баллах 3"]


def test_overlap_reports_real_overlap_range(tree: Path, edit: Edit) -> None:
    def change(d: dict[str, Any]) -> None:
        d["scores"][0]["bands"][0].update(max=6)  # 0–6 поверх 4–6
        d["scores"][0]["bands"][1].update(min=4, max=5)
        d["scores"][0]["bands"][2].update(min=7)

    edit(SCALE, change)

    assert messages(tree) == [f"{SCALE}: scores[0].bands: пороги пересекаются на баллах 4–5"]


def test_bands_must_cover_whole_range(tree: Path, edit: Edit) -> None:
    edit(SCALE, lambda d: d["scores"][0]["bands"][2].update(max=8))

    assert messages(tree) == [f"{SCALE}: scores[0].bands: пропущены баллы 9"]


def test_band_beyond_score_range(tree: Path, edit: Edit) -> None:
    edit(SCALE, lambda d: d["scores"][0]["bands"][2].update(max=10))

    assert messages(tree) == [
        f"{SCALE}: scores[0].bands[2]: порог 7–10 выходит за диапазон суммы 0–9"
    ]


def test_band_min_not_above_max(tree: Path, edit: Edit) -> None:
    edit(SCALE, lambda d: d["scores"][0]["bands"][0].update(min=4, max=3))

    assert messages(tree) == [f"{SCALE}: scores[0].bands[0]: min больше max"]


def test_duplicate_item_id(tree: Path, edit: Edit) -> None:
    edit(SCALE, lambda d: d["items"].append({**d["items"][0]}))

    assert messages(tree) == [f"{SCALE}: items[3].id: повторяется q1"]


def test_duplicate_score_id(tree: Path, edit: Edit) -> None:
    edit(SCALE, lambda d: d["scores"].append({**d["scores"][0]}))

    assert messages(tree) == [f"{SCALE}: scores[2].id: повторяется total"]


def test_duplicate_item_in_score_skips_band_check(tree: Path, edit: Edit) -> None:
    edit(SCALE, lambda d: d["scores"][0].update(items=["q1", "q2", "q2"]))

    assert messages(tree) == [f"{SCALE}: scores[0].items[2]: повторяется q2"]


def test_duplicate_band_code(tree: Path, edit: Edit) -> None:
    edit(SCALE, lambda d: d["scores"][0]["bands"][1].update(code="minimal"))

    assert messages(tree) == [f"{SCALE}: scores[0].bands[1].code: повторяется minimal"]


def test_score_id_must_differ_from_item_ids(tree: Path, edit: Edit) -> None:
    edit(SCALE, lambda d: d["scores"][1].update(id="q1"))

    assert messages(tree) == [f"{SCALE}: scores[1].id: совпадает с id пункта q1"]


# --- правила ---------------------------------------------------------------


def test_rule_scale_must_exist(tree: Path, edit: Edit) -> None:
    edit(ALERT, lambda d: d["when"]["any"][0].update(scale="nope"))

    assert messages(tree) == [f"{ALERT}: when.any[0].scale: нет шкалы nope"]


def test_rule_item_must_exist(tree: Path, edit: Edit) -> None:
    edit(ALERT, lambda d: d["when"]["any"][0].update(item="q10"))

    assert messages(tree) == [f"{ALERT}: when.any[0].item: нет пункта q10 в шкале example-mood"]


def test_rule_score_must_exist(tree: Path, edit: Edit) -> None:
    edit(ROUTING, lambda d: d["when"]["all"][0].update(score="anxiety"))

    assert messages(tree) == [
        f"{ROUTING}: when.all[0].score: нет суммы anxiety в шкале example-mood"
    ]


def test_rule_value_must_be_in_item_range(tree: Path, edit: Edit) -> None:
    edit(ALERT, lambda d: d["when"]["any"][0].update(value=4))

    assert messages(tree) == [f"{ALERT}: when.any[0].value: 4 вне диапазона 0–3"]


def test_rule_value_must_be_in_score_range(tree: Path, edit: Edit) -> None:
    edit(ROUTING, lambda d: d["when"]["all"][0].update(value=10))

    assert messages(tree) == [f"{ROUTING}: when.all[0].value: 10 вне диапазона 0–9"]


def test_nested_leaf_path_is_reported(tree: Path, edit: Edit) -> None:
    edit(ROUTING, lambda d: d["when"]["all"][1]["any"][0].update(item="q10"))

    assert messages(tree) == [
        f"{ROUTING}: when.all[1].any[0].item: нет пункта q10 в шкале example-mood"
    ]


def test_alert_must_be_mandatory(tree: Path, edit: Edit) -> None:
    edit(ALERT, lambda d: d.update(level="desirable"))

    assert messages(tree) == [f"{ALERT}: level: у alert уровень всегда mandatory"]


def test_rule_needs_quiet_case(tree: Path, edit: Edit) -> None:
    edit(ALERT, lambda d: d.update(cases=d["cases"][:1]))

    assert messages(tree) == [f"{ALERT}: cases: нужен хотя бы один случай с fires: false"]


def test_rule_needs_firing_case(tree: Path, edit: Edit) -> None:
    edit(ALERT, lambda d: d.update(cases=d["cases"][1:]))

    assert messages(tree) == [f"{ALERT}: cases: нужен хотя бы один случай с fires: true"]


def test_rule_case_scale_must_exist(tree: Path, edit: Edit) -> None:
    edit(ALERT, lambda d: d["cases"][1].update(given={"nope": {"q3": 0}}))

    assert messages(tree) == [f"{ALERT}: cases[1].given.nope: нет шкалы nope"]


def test_rule_case_key_must_exist(tree: Path, edit: Edit) -> None:
    edit(ALERT, lambda d: d["cases"][1].update(given={"example-mood": {"q9": 0}}))

    assert messages(tree) == [
        f"{ALERT}: cases[1].given.example-mood.q9: нет пункта или суммы q9 в шкале example-mood"
    ]


def test_rule_case_must_match_evaluation(tree: Path, edit: Edit) -> None:
    edit(ALERT, lambda d: d["cases"][1]["given"]["example-mood"].update(q3=2))

    assert messages(tree) == [f"{ALERT}: cases[1]: правило срабатывает, а ожидается fires: false"]


# --- сценарии ---------------------------------------------------------------


def test_scenario_rule_refs_must_exist(tree: Path, edit: Edit) -> None:
    edit(SCENARIO, lambda d: d["steps"][2]["refs"].update(rules=["nope"]))

    assert messages(tree) == [f"{SCENARIO}: steps[2].refs.rules[0]: нет правила nope"]


def test_scenario_scale_refs_must_exist(tree: Path, edit: Edit) -> None:
    edit(SCENARIO, lambda d: d["steps"][1]["refs"].update(scales=["nope"]))

    assert messages(tree) == [f"{SCENARIO}: steps[1].refs.scales[0]: нет шкалы nope"]


def test_duplicate_step_id(tree: Path, edit: Edit) -> None:
    edit(SCENARIO, lambda d: d["steps"][1].update(id="s1"))

    assert messages(tree) == [f"{SCENARIO}: steps[1].id: повторяется s1"]


def test_clinical_step_needs_source(tree: Path, edit: Edit) -> None:
    edit(SCENARIO, lambda d: d["steps"][1].update(sources=[]))

    assert messages(tree) == [f"{SCENARIO}: steps[1].sources: у клинического шага нужен источник"]


def test_system_step_needs_source(tree: Path, edit: Edit) -> None:
    def change(d: dict[str, Any]) -> None:
        d["steps"][2]["refs"] = {}
        d["steps"][2]["sources"] = []

    edit(SCENARIO, change)

    assert messages(tree) == [f"{SCENARIO}: steps[2].sources: у клинического шага нужен источник"]


# --- истории болезни --------------------------------------------------------


def _set_fact(tree: Path, field: str, history: str, quote: str) -> None:
    """Текст истории и единственный факт в поле `field`; другое поле пустое."""
    (tree / "cases/case-01/history.md").write_text(history, encoding="utf-8")
    path = tree / CASE
    data = json.loads(path.read_text(encoding="utf-8"))
    data.update(risk_factors=[], anamnesis=[])
    data[field] = [{"fact": "факт", "quote": quote}]
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


@pytest.mark.parametrize("field", ["risk_factors", "anamnesis"])
def test_case_quote_must_be_in_history(tree: Path, field: str) -> None:
    _set_fact(tree, field, "Анамнез: сниженное настроение около месяца.", "плохой сон")

    assert messages(tree) == [f"{CASE}: {field}[0].quote: цитаты нет в history.md: «плохой сон»"]


@pytest.mark.parametrize(
    ("history", "quote"),
    [
        ("Сниженное настроение около месяца.", "сниженное  НАСТРОЕНИЕ"),  # регистр, пробелы
        ("Анамнез:\nсниженное настроение", "анамнез: сниженное"),  # перевод строки
        ("Всё хорошо", "все хорошо"),  # ё = е
        ("Мать страдала депрессией.", "«Мать страдала депрессией.»"),  # кавычки и точка по краям
    ],
)
def test_case_quote_found_ignoring_case_spaces_and_edges(
    tree: Path, history: str, quote: str
) -> None:
    _set_fact(tree, "anamnesis", history, quote)

    assert messages(tree) == []


@pytest.mark.parametrize(
    ("history", "quote"),
    [
        ("Спит неплохо", "плохо"),  # часть слова не считается
        ("Сниженное настроение", "настроен"),
        ("Сниженное настроение", "«»"),  # после обрезки кавычек пусто
    ],
)
def test_case_quote_must_match_whole_words(tree: Path, history: str, quote: str) -> None:
    _set_fact(tree, "anamnesis", history, quote)

    assert messages(tree) == [f"{CASE}: anamnesis[0].quote: цитаты нет в history.md: «{quote}»"]
