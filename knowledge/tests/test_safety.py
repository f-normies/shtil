"""Исполнение условий правил безопасности препарата (спека #29, разделы 3–4)."""

from datetime import date

import pytest

from clin_compass_knowledge.safety import Facts, Medication, evaluate, icd10_matches
from clin_compass_knowledge.schemas.drug import SafetyWhen
from clin_compass_knowledge.schemas.drug_group import DrugGroup

TODAY = date(2026, 10, 18)
SOURCE = {"source": "example-label", "locator": "4.5"}
GROUPS = {
    "maoi": DrugGroup.model_validate(
        {
            "id": "maoi",
            "title": "Ингибиторы МАО",
            "inn": ["моклобемид", "селегилин"],
            "sources": [SOURCE],
        }
    ),
    "sedative": DrugGroup.model_validate(
        {
            "id": "sedative",
            "title": "Седативные",
            "inn": ["фенобарбитал", "дифенгидрамин", "зверобоя трава"],
            "sources": [SOURCE],
        }
    ),
}


def when(*conditions: dict[str, object]) -> SafetyWhen:
    return SafetyWhen.model_validate({"any": list(conditions)})


def run(condition: dict[str, object], **facts: object) -> str:
    return evaluate(when(condition), Facts(today=TODAY, **facts), GROUPS).outcome  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("codes", "diagnoses", "outcome"),
    [
        (["G40"], {"G40.1"}, "yes"),
        (["G40"], {"G41.0"}, "no"),
        (["F00-F09"], {"F06.4"}, "yes"),
        (["F00-F09"], {"F10.2"}, "no"),
        (["E74.3"], {"E74.3"}, "yes"),
        (["E74.3"], {"E74.2"}, "no"),
        (["G40"], set(), "no"),
        # Диапазон подрубрик: циррозы печени K74.3–K74.6, но не фиброз K74.0.
        (["K74.3-K74.6"], {"K74.4"}, "yes"),
        (["K74.3-K74.6"], {"K74.6"}, "yes"),
        (["K74.3-K74.6"], {"K74.0"}, "no"),
        (["K74.3-K74.6"], {"K74"}, "no"),
        # Код с двумя знаками после точки входит в подрубрику, как и в диапазон подрубрик.
        (["K70.3"], {"K70.30"}, "yes"),
        (["K74.3-K74.6"], {"K74.69"}, "yes"),
        (["K70.3"], {"K70.4"}, "no"),
        (["K70.3"], {"K70"}, "no"),
    ],
)
def test_diagnosis_codes_and_ranges(codes: list[str], diagnoses: set[str], outcome: str) -> None:
    assert run({"diagnosis": codes}, diagnoses=frozenset(diagnoses)) == outcome


@pytest.mark.parametrize(
    ("code", "patterns", "expected"),
    [
        # Рубрика: G40 покрывает G40.1, но не G41.0.
        ("G40.1", ("G40",), True),
        ("G41.0", ("G40",), False),
        # Код целиком.
        ("E74.3", ("E74.3",), True),
        ("E74.2", ("E74.3",), False),
        # Подрубрика: K70.3 покрывает K70.30, но не K70.4 и не рубрику K70.
        ("K70.30", ("K70.3",), True),
        ("K70.4", ("K70.3",), False),
        ("K70", ("K70.3",), False),
        # Диапазоны рубрик и подрубрик.
        ("F06.4", ("F00-F09",), True),
        ("F10.2", ("F00-F09",), False),
        ("K74.4", ("K74.3-K74.6",), True),
        ("K74.6", ("K74.3-K74.6",), True),
        ("K74.69", ("K74.3-K74.6",), True),
        ("K74.0", ("K74.3-K74.6",), False),
        ("K74", ("K74.3-K74.6",), False),
        # Хватает одного шаблона из нескольких.
        ("F33.1", ("F32", "F33"), True),
        ("F41.1", (), False),
    ],
)
def test_icd10_matches(code: str, patterns: tuple[str, ...], expected: bool) -> None:
    assert icd10_matches(code, patterns) is expected


@pytest.mark.parametrize(
    ("values", "outcome"),
    [({"egfr": 59.9}, "yes"), ({"egfr": 60}, "no"), ({}, "unknown")],
)
def test_fact_comparison_and_unknown_value(values: dict[str, object], outcome: str) -> None:
    assert run({"fact": {"name": "egfr", "op": "<", "value": 60}}, values=values) == outcome


@pytest.mark.parametrize(
    ("values", "outcome"),
    [({"child_pugh": "B"}, "yes"), ({"child_pugh": "none"}, "no"), ({}, "unknown")],
)
def test_fact_in_list(values: dict[str, object], outcome: str) -> None:
    condition = {"fact": {"name": "child_pugh", "in": ["A", "B", "C"]}}

    assert run(condition, values=values) == outcome


@pytest.mark.parametrize(
    ("sex", "values", "outcome"),
    [
        ("female", {"pregnant": True}, "yes"),
        ("female", {"pregnant": False}, "no"),
        ("female", {}, "unknown"),
        (None, {}, "unknown"),
        # У мужчины беременность и грудное вскармливание неприменимы.
        ("male", {}, "no"),
    ],
)
def test_pregnancy_depends_on_sex(sex: str | None, values: dict[str, object], outcome: str) -> None:
    condition = {"fact": {"name": "pregnant", "op": "==", "value": True}}

    assert run(condition, sex=sex, values=values) == outcome


def stopped(days_ago: int) -> Medication:
    return Medication(
        name="Аурорикс",
        inn="Моклобемид",
        started_on=None,
        stopped_on=date.fromordinal(TODAY.toordinal() - days_ago),
    )


def taking(name: str, inn: str) -> Medication:
    return Medication(name=name, inn=inn, started_on=None, stopped_on=None)


@pytest.mark.parametrize(
    ("medication", "outcome"),
    [
        (Medication(name="Аурорикс", inn="моклобемид", started_on=None, stopped_on=None), "yes"),
        (stopped(13), "yes"),
        # 14-й день после отмены ещё «в течение 2 недель»: безопасное прочтение (ревью медика #76).
        (stopped(14), "yes"),
        (stopped(15), "no"),
        (Medication(name="Золофт", inn="сертралин", started_on=None, stopped_on=None), "no"),
        # Без МНН группа не сопоставляется: об этом говорит отдельная проверка therapy.
        (Medication(name="Неизвестин", inn=None, started_on=None, stopped_on=None), "no"),
        # Комбинированный МНН из ГРЛС: компонент из группы делает условие истинным.
        (taking("Комбин", "Парацетамол+Селегилин"), "yes"),
        (taking("Залдиар", "Парацетамол+Трамадол"), "no"),
    ],
)
def test_medication_group_with_days_after_stop(medication: Medication, outcome: str) -> None:
    condition = {"medication": {"group": "maoi", "within_days": 14}}

    assert run(condition, medications=(medication,)) == outcome


@pytest.mark.parametrize(
    ("name", "inn"),
    [
        # Части комбинации через «;» и квадратные скобки — как в снимке ГРЛС.
        (
            "Антигриппин-АНВИ",
            "Ацетилсалициловая кислота+[Аскорбиновая кислота+Рутозид]; "
            "Дифенгидрамин+Метамизол натрия+[Кальция глюконат]",
        ),
        # Дневная и ночная таблетки через « и ».
        ("Викс День и Ночь", "Парацетамол+Фенилэфрин и Дифенгидрамин+Парацетамол"),
        # Соль: член группы — начало компонента до целого слова.
        ("Фенобарбитал натрия", "Фенобарбитал натрия"),
    ],
)
def test_combined_inn_parts_match_the_group(name: str, inn: str) -> None:
    assert run({"medication": {"group": "sedative"}}, medications=(taking(name, inn),)) == "yes"


@pytest.mark.parametrize(
    "inn",
    [
        # Начало слова — не совпадение: «зверобоя травы экстракт» не «зверобоя трава».
        "Зверобоя продырявленного травы экстракт",
        "Зверобоя травы экстракт",
        "Фенобарбиталовая кислота",
    ],
)
def test_group_member_matches_only_whole_words(inn: str) -> None:
    assert run({"medication": {"group": "sedative"}}, medications=(taking("Х", inn),)) == "no"


def test_combination_reason_names_the_matching_component() -> None:
    combo = taking("Комбин", "Парацетамол+Селегилин")

    result = evaluate(
        when({"medication": {"group": "maoi"}}), Facts(today=TODAY, medications=(combo,)), GROUPS
    )

    assert result.reasons == ("Комбин (селегилин): принимает",)


def test_stopped_medication_without_window_does_not_count() -> None:
    assert run({"medication": {"group": "maoi"}}, medications=(stopped(1),)) == "no"


def test_until_is_the_first_allowed_day() -> None:
    result = evaluate(
        when({"medication": {"group": "maoi", "within_days": 14}}),
        Facts(today=TODAY, medications=(stopped(10),)),
        GROUPS,
    )

    # Отменён 08.10: 22.10 — 14-й день, ещё противопоказан; можно с 23.10.
    assert result.until == date(2026, 10, 23)
    assert result.reasons == ("Аурорикс (моклобемид): отменён 08.10.2026",)


def test_ask_is_always_unknown() -> None:
    assert run({"ask": "гиперчувствительность к маритупирдину"}) == "unknown"


def test_any_yes_wins_over_unknown() -> None:
    result = evaluate(
        when({"fact": {"name": "egfr", "op": "<", "value": 60}}, {"diagnosis": ["G40"]}),
        Facts(today=TODAY, diagnoses=frozenset({"G40.1"})),
        GROUPS,
    )

    assert result.outcome == "yes"
    assert result.reasons == ("диагноз G40.1",)


def test_unknown_explains_what_to_find_out() -> None:
    result = evaluate(
        when({"fact": {"name": "egfr", "op": "<", "value": 60}}), Facts(today=TODAY), GROUPS
    )

    assert result.reasons == ("СКФ не указана",)


def test_no_until_while_still_taking() -> None:
    taking = Medication(name="Аурорикс", inn="моклобемид", started_on=None, stopped_on=None)
    result = evaluate(
        when({"medication": {"group": "maoi", "within_days": 14}}),
        Facts(today=TODAY, medications=(taking, stopped(10))),
        GROUPS,
    )

    assert (result.outcome, result.until) == ("yes", None)
