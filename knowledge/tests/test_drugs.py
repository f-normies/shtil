"""Справочник препаратов: схема и перекрёстные проверки (спека #28, раздел 4)."""

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from clin_compass_knowledge.grls import load_registry
from clin_compass_knowledge.loader import load_knowledge
from clin_compass_knowledge.schemas.drug import Form
from tests.helpers import FIXTURES, VALID, Edit, messages

DRUG = "drugs/example-drug.yaml"


def test_valid_drug_loads() -> None:
    drug = load_knowledge(VALID).drugs["example-drug"]

    assert drug.registration.number == "ЛП-000100"
    assert [i.id for i in drug.indications] == ["anxiety", "course"]
    course = drug.indications[1]
    assert (course.requires_any, course.manual) == (
        ("U09.9",),
        ("лёгкая или средняя степень выраженности",),
    )
    assert (course.regimen.duration.days, course.regimen.duration.max_days) == (28, 42)


def test_form_appearance_uses_typed_shape_and_color() -> None:
    form = Form.model_validate(
        {
            "id": "tab-10",
            "title": "таблетки, 10 мг",
            "strength_mg": 10,
            "appearance": {
                "shape": "round",
                "color": "white",
                "description": "Круглая белая таблетка.",
                "source": {"source": "example-label", "locator": "3"},
            },
        }
    )

    assert form.appearance is not None
    assert (form.appearance.shape, form.appearance.color) == ("round", "white")


@pytest.mark.parametrize(("field", "value"), [("shape", "triangle"), ("color", "purple")])
def test_form_appearance_rejects_values_outside_the_supported_set(field: str, value: str) -> None:
    appearance = {
        "shape": "round",
        "color": "white",
        "description": "Круглая белая таблетка.",
        "source": {"source": "example-label", "locator": "3"},
    }
    appearance[field] = value

    with pytest.raises(ValidationError) as error:
        Form.model_validate(
            {
                "id": "tab-10",
                "title": "таблетки, 10 мг",
                "strength_mg": 10,
                "appearance": appearance,
            }
        )

    assert any(
        issue["loc"] == ("appearance", field) and issue["type"] == "literal_error"
        for issue in error.value.errors()
    )


def set_label(data: Any) -> None:
    data["label"] = "example-guideline"


def set_leaflet_label(data: Any) -> None:
    data["label"] = "example-leaflet"


def set_form(data: Any) -> None:
    data["indications"][0]["regimen"]["form"] = "tab-99"


def set_number(number: str) -> Any:
    def change(data: Any) -> None:
        data["registration"]["number"] = number

    return change


def set_code(data: Any) -> None:
    data["indications"][0]["icd10"] = ["F41,1"]


def set_duration(data: Any) -> None:
    data["indications"][1]["regimen"]["duration"] = {"days": 42, "max_days": 28, "text": "x"}


def set_unknown_source(data: Any) -> None:
    data["age_min"]["source"]["source"] = "no-such-source"


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        (set_label, f"{DRUG}: label: example-guideline — не инструкция (kind: drug_label)"),
        # Листок-вкладыш написан для пациента; для врача источник — ОХЛП.
        (set_leaflet_label, f"{DRUG}: label: example-leaflet — не инструкция (kind: drug_label)"),
        (set_form, f"{DRUG}: indications[0].regimen.form: нет формы tab-99 в forms"),
        (
            set_number("ЛП-999999"),
            f"{DRUG}: registration.number: нет в снимке реестра example-registry",
        ),
        (set_number("ЛП-000101"), f"{DRUG}: registration.number: в снимке реестра статус inactive"),
        (
            set_unknown_source,
            f"{DRUG}: age_min.source.source: нет источника no-such-source в манифесте",
        ),
    ],
)
def test_cross_checks(tree: Path, edit: Edit, change: Any, expected: str) -> None:
    edit(DRUG, change)

    assert expected in messages(tree)


@pytest.mark.parametrize("change", [set_code, set_duration])
def test_schema_rejects(tree: Path, edit: Edit, change: Any) -> None:
    edit(DRUG, change)

    assert any(m.startswith(f"{DRUG}:") for m in messages(tree))


def test_registry_source_must_be_a_registry(tree: Path, edit: Edit) -> None:
    def change(data: Any) -> None:
        data["registration"]["source"]["source"] = "example-label"

    edit(DRUG, change)

    expected = "example-label — не реестр (kind: drug_registry с файлом)"
    assert f"{DRUG}: registration.source.source: {expected}" in messages(tree)


def test_registry_finds_entry_by_number() -> None:
    registry = load_registry(FIXTURES / "grls" / "registry.csv")

    entry = registry.by_number("ЛП-№(002445)-(РГ-RU)")

    assert entry is not None
    assert (entry.inn, entry.status) == ("Маритупирдин", "active")
    assert registry.by_number("ЛП-404") is None


def test_safety_rules_and_groups_load() -> None:
    knowledge = load_knowledge(VALID)
    drug = knowledge.drugs["example-drug"]

    assert [r.id for r in drug.contraindications] == ["maoi", "hypersensitivity"]
    assert [r.id for r in drug.cautions] == ["renal"]
    assert knowledge.drug_groups["example-maoi"].inn == ("примеромоклобемид",)


def set_group(data: Any) -> None:
    data["contraindications"][0]["when"]["any"][0]["medication"]["group"] = "no-such-group"


def set_wrong_result(data: Any) -> None:
    data["contraindications"][0]["cases"][1]["result"] = "yes"


def drop_no_case(data: Any) -> None:
    data["cautions"][0]["cases"] = [data["cautions"][0]["cases"][0]]


def set_reportable_without_words(data: Any) -> None:
    data["adverse_reactions"][1]["patient_reportable"] = True


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        (
            set_group,
            f"{DRUG}: contraindications[0].when.any[0].medication.group: нет группы no-such-group",
        ),
        (set_wrong_result, f"{DRUG}: contraindications[0].cases[1]: ожидалось yes, получилось no"),
        (drop_no_case, f"{DRUG}: cautions[0].cases: нужен случай с result: no"),
    ],
)
def test_safety_cross_checks(tree: Path, edit: Edit, change: Any, expected: str) -> None:
    edit(DRUG, change)

    assert expected in messages(tree)


def test_reportable_reaction_needs_patient_words(tree: Path, edit: Edit) -> None:
    edit(DRUG, set_reportable_without_words)

    assert any(m.startswith(f"{DRUG}: adverse_reactions") for m in messages(tree))


def test_reaction_actions_load() -> None:
    drug = load_knowledge(VALID).drugs["example-drug"]

    assert drug.reaction_actions is not None
    assert drug.reaction_actions.doctor_actions == ("Оценить состояние пациента",)
    assert drug.additional_monitoring


def drop_reaction_actions(data: Any) -> None:
    del data["reaction_actions"]


def set_reaction_actions_source(data: Any) -> None:
    data["reaction_actions"]["sources"][0]["source"] = "no-such-source"


def test_reportable_reactions_need_doctor_actions(tree: Path, edit: Edit) -> None:
    # Отметка пациента поднимает сигнал врачу, а у сигнала должны быть действия врача.
    edit(DRUG, drop_reaction_actions)

    assert any(m.startswith(f"{DRUG}: reaction_actions") for m in messages(tree))


def test_reaction_actions_source_is_checked(tree: Path, edit: Edit) -> None:
    edit(DRUG, set_reaction_actions_source)

    expected = "reaction_actions.sources[0].source: нет источника no-such-source в манифесте"
    assert f"{DRUG}: {expected}" in messages(tree)


def set_form_appearance(data: Any, source: str) -> None:
    data["forms"][0]["appearance"] = {
        "shape": "round",
        "color": "white",
        "description": "Круглая белая таблетка.",
        "source": {"source": source, "locator": "3"},
    }


def test_form_appearance_source_must_exist(tree: Path, edit: Edit) -> None:
    edit(DRUG, lambda data: set_form_appearance(data, "no-such-source"))

    expected = "forms[0].appearance.source.source: нет источника no-such-source в манифесте"
    assert f"{DRUG}: {expected}" in messages(tree)


def test_form_appearance_source_must_be_a_drug_label(tree: Path, edit: Edit) -> None:
    edit(DRUG, lambda data: set_form_appearance(data, "example-guideline"))

    expected = (
        "forms[0].appearance.source.source: example-guideline — не инструкция (kind: drug_label)"
    )
    assert f"{DRUG}: {expected}" in messages(tree)


def test_ask_only_rule_needs_no_yes_or_no_case() -> None:
    # Гиперчувствительность всегда спрашивается у врача: случаев «да» и «нет» у неё нет.
    assert not [m for m in messages(VALID) if "hypersensitivity" in m]


def add_ask(data: Any) -> None:
    # Правило с вопросом врачу никогда не даёт «нет»: случай «нет» становится «неизвестно».
    rule = data["contraindications"][0]
    rule["when"]["any"].append({"ask": "нет ли другого ингибитора МАО"})
    rule["cases"][1]["result"] = "unknown"


def add_ask_without_unknown(data: Any) -> None:
    rule = data["contraindications"][0]
    rule["when"]["any"].append({"ask": "нет ли другого ингибитора МАО"})
    rule["cases"] = [rule["cases"][0]]


def test_rule_with_a_question_needs_yes_and_unknown_cases(tree: Path, edit: Edit) -> None:
    edit(DRUG, add_ask)

    assert not [m for m in messages(tree) if "contraindications[0]" in m]


def test_rule_with_a_question_without_unknown_case_is_reported(tree: Path, edit: Edit) -> None:
    edit(DRUG, add_ask_without_unknown)

    assert f"{DRUG}: contraindications[0].cases: нужен случай с result: unknown" in messages(tree)


@pytest.mark.parametrize("pattern", ["K74.3-K75", "F09-F00"])
def test_diagnosis_range_bounds_must_match_and_be_ordered(
    tree: Path, edit: Edit, pattern: str
) -> None:
    def change(data: Any) -> None:
        data["cautions"][0]["when"]["any"] = [{"diagnosis": [pattern]}]

    edit(DRUG, change)

    assert any(m.startswith(f"{DRUG}: cautions[0].when.any[0]") for m in messages(tree))
