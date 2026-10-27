"""Справочник препаратов этого репозитория против ОХЛП (спека #28, разделы 2 и 4).

Числа — из ОХЛП «Авиандра» версии 0010 (`aviandr-ohlp`). Раздел указан у каждого случая.
"""

import csv

import pytest

from clin_compass_knowledge.loader import load_knowledge
from clin_compass_knowledge.safety import Facts, Medication, components, evaluate
from clin_compass_knowledge.schemas.drug import CASE_TODAY, SafetyRule
from tests.helpers import REPO_KNOWLEDGE

KNOWLEDGE = load_knowledge(REPO_KNOWLEDGE)
AVIANDR = KNOWLEDGE.drugs["aviandr"]
GROUPS = KNOWLEDGE.drug_groups
LEAFLET_NOW = "раздел 4, «Немедленно сообщите лечащему врачу»"
LEAFLET_OTHER = "раздел 4, «Другие возможные нежелательные реакции»"


def rule(rule_id: str) -> SafetyRule:
    return next(r for r in (*AVIANDR.contraindications, *AVIANDR.cautions) if r.id == rule_id)


def outcome(rule_id: str, *codes: str) -> str:
    facts = Facts(today=CASE_TODAY, diagnoses=frozenset(codes))
    return evaluate(rule(rule_id).when, facts, GROUPS).outcome


def registry_inns() -> set[str]:
    """Все МНН снимка ГРЛС: так их видит сверка препарата пациента с группой."""
    path = REPO_KNOWLEDGE / "sources" / "files" / "grls.csv"
    with path.open(encoding="utf-8", newline="") as fh:
        return {row["inn"] for row in csv.DictReader(fh) if row["inn"].strip()}


def taking(rule_id: str, inn: str) -> str:
    facts = Facts(today=CASE_TODAY, medications=(Medication(inn, inn, None, None),))
    return evaluate(rule(rule_id).when, facts, GROUPS).outcome


def test_registration_and_label() -> None:
    # Раздел 8: ЛП-№(002445)-(РГ-RU); МНН — раздел 2.
    assert AVIANDR.registration.number == "ЛП-№(002445)-(РГ-RU)"
    assert AVIANDR.inn == "маритупирдин"
    assert AVIANDR.label == "aviandr-ohlp"


def test_only_tablet_20_mg() -> None:
    # Разделы 2 и 3: таблетки, покрытые плёночной оболочкой, 20 мг.
    assert [(f.id, f.strength_mg) for f in AVIANDR.forms] == [("tab-20", 20)]


def test_aviandr_tablet_appearance_is_sourced_from_label_section_3() -> None:
    appearance = AVIANDR.forms[0].appearance

    assert appearance is not None
    assert (appearance.shape, appearance.color) == ("round", "white")
    assert appearance.description == (
        "Круглые двояковыпуклые таблетки, покрытые пленочной оболочкой белого или почти "
        "белого цвета. На поперечном разрезе ядро таблетки белого или почти белого цвета."
    )
    assert (appearance.source.source, appearance.source.locator) == (
        "aviandr-ohlp",
        "3, «Лекарственная форма»",
    )
    assert KNOWLEDGE.sources[appearance.source.source].kind == "drug_label"


def test_adults_only() -> None:
    # Раздел 4.1: «у взрослых старше 18 лет»; 4.2: детям до 18 лет не назначать.
    assert AVIANDR.age_min.years == 18
    assert AVIANDR.age_min.source.locator.startswith("4.1")


def test_gad_regimen() -> None:
    # Раздел 4.2: по 1 таблетке (20 мг) 2 раза в сутки (утром и вечером); длительность —
    # врач, заболевание с хроническим течением.
    gad = next(i for i in AVIANDR.indications if i.id == "gad")
    assert gad.icd10 == ("F41.1",)
    assert (gad.regimen.dose_mg, gad.regimen.times_per_day) == (20, 2)
    assert gad.regimen.duration.days is None


def test_post_covid_regimen() -> None:
    # Раздел 4.1: тревожные состояния после COVID-19 при стрессовой реакции и нарушении
    # адаптации лёгкой и средней степени; 4.2: курс 4 недели, при необходимости до 6.
    post = next(i for i in AVIANDR.indications if i.id == "post-covid-anxiety")
    # F43.0 (острая реакция на стресс) не входит: проходит за часы или дни, а курс — 4–6 недель.
    assert set(post.icd10) == {"F43.2", "F43.8"}
    assert post.requires_any == ("U09.9",)
    assert post.manual == ("лёгкая или средняя степень выраженности",)
    assert (post.regimen.dose_mg, post.regimen.times_per_day) == (20, 2)
    assert (post.regimen.duration.days, post.regimen.duration.max_days) == (28, 42)


def test_every_reference_points_to_the_label_or_registry() -> None:
    refs = [AVIANDR.age_min.source, AVIANDR.registration.source]
    refs += [i.source for i in AVIANDR.indications] + [
        i.regimen.source for i in AVIANDR.indications
    ]
    refs += [i.source for i in AVIANDR.instructions]

    assert {r.source for r in refs} == {"aviandr-ohlp", "grls"}


def test_label_contraindications_are_rules() -> None:
    # ОХЛП 4.3: гиперчувствительность, эпилепсия и органические заболевания мозга,
    # ингибиторы МАО и 2 недели после отмены, СКФ < 60, печёночная недостаточность A–C;
    # 4.6: беременность и грудное вскармливание; 4.4: непереносимость лактозы.
    assert [r.id for r in AVIANDR.contraindications] == [
        "hypersensitivity",
        "epilepsy-organic",
        "maoi",
        "renal",
        "hepatic",
        "pregnancy",
        "breastfeeding",
        "lactose",
    ]


def test_label_cautions_are_rules() -> None:
    # ОХЛП 4.4: сердечно-сосудистые заболевания; 4.5: седативные, серотонинергические,
    # ингибиторы CYP2D6.
    assert [r.id for r in AVIANDR.cautions] == [
        "cardiovascular",
        "sedative",
        "serotonergic",
        "cyp2d6",
    ]


def test_scenario_drugs_are_in_their_groups() -> None:
    # Сценарий 3: моклобемид — ингибитор МАО; сценарий 2: суматриптан — триптан (4.5).
    assert "моклобемид" in GROUPS["maoi"].inn
    assert "суматриптан" in GROUPS["serotonergic"].inn


@pytest.mark.parametrize(
    ("group", "inn"),
    [
        # Ревью медика #76: классы 4.5 целиком — бензодиазепины и нейролептики.
        ("sedative", "оксазепам"),
        ("sedative", "медазепам"),
        # Гидазепам: в ГРЛС МНН записан химическим названием.
        ("sedative", "гидразинокарбонилметилбромфенилдигидробенздиазепин"),
        ("sedative", "тофизопам"),
        ("sedative", "левомепромазин"),
        ("sedative", "хлорпротиксен"),
        ("sedative", "клозапин"),
        ("sedative", "сульпирид"),
        # 4.5: осторожность «с серотонинергическими препаратами» в целом.
        ("serotonergic", "венлафаксин"),
        ("serotonergic", "дулоксетин"),
        ("serotonergic", "милнаципран"),
        ("serotonergic", "вортиоксетин"),
        ("serotonergic", "тразодон"),
        # Зверобой: все написания МНН в ГРЛС, иначе выбор врача даёт молчаливый `pass`.
        ("serotonergic", "зверобоя продырявленного трава"),
        ("serotonergic", "зверобоя трава"),
        ("serotonergic", "зверобоя продырявленного травы экстракт"),
        ("maoi", "линезолид"),
        ("maoi", "сафинамид"),
        ("cyp2d6-inhibitors", "бупропион"),
        # Повторное ревью #76: классы 4.5 и листка-вкладыша (раздел 2) целиком.
        *(
            ("sedative", inn)
            for inn in (
                "хлордиазепоксид",
                "клобазам",
                "мидазолам",
                "арипипразол",
                "флупентиксол",
                "зуклопентиксол",
                "перфеназин",
                "трифлуоперазин",
                "тиоридазин",
                "перициазин",
                "амисульприд",
                "палиперидон",
                "зипрасидон",
                "луразидон",
                "карипразин",
                "флуфеназин",
                "тиаприд",
                "бензобарбитал",
                "примидон",
                "доксиламин",
                "диметинден",
                "мебгидролин",
                "хифенадин",
                "зопиклон",
                "золпидем",
                "цетиризин",
            )
        ),
        *(
            ("serotonergic", inn)
            for inn in (
                "пипофезин",
                "доксепин",
                "буспирон",
                "миртазапин",
                "миансерин",
                "тапентадол",
                "декстрометорфан",
                "зверобоя травы экстракт",
                "зверобоя травы экстракт сухой",
            )
        ),
    ],
)
def test_group_members_after_medic_review(group: str, inn: str) -> None:
    assert inn in GROUPS[group].inn


def test_duloxetine_is_not_a_cyp2d6_inhibitor_of_maritupirdine() -> None:
    # 4.5: дулоксетин не менял фармакокинетику маритупирдина.
    assert "дулоксетин" not in GROUPS["cyp2d6-inhibitors"].inn


# Названы в ОХЛП 4.5 примерами, но в РФ не зарегистрированы: в снимке ГРЛС их нет.
NOT_IN_REGISTRY = {"триптофан", "хинидин"}


def test_group_members_are_spelled_as_in_the_registry() -> None:
    # МНН сверяется с группой по написанию из снимка ГРЛС: опечатка даёт молчаливый `pass`.
    # Член группы — МНН снимка или действующее вещество комбинированного МНН
    # (декстрометорфан есть в снимке только в комбинациях).
    registry = {part for inn in registry_inns() for part in components(inn)}
    missing = {
        inn
        for group in GROUPS.values()
        for inn in group.inn
        if components(inn) != [inn.lower().replace("ё", "е")] or inn.lower() not in registry
    }

    assert missing == NOT_IN_REGISTRY


# Гомеопатические формы зверобоя: действующего вещества в значимой дозе в них нет.
HOMEOPATHIC_ST_JOHNS_WORT = {"Зверобоя продырявленного травы настойка гомеопатическая"}


def test_every_st_johns_wort_inn_in_the_registry_is_serotonergic() -> None:
    # Повторное ревью #76: у зверобоя в снимке пять написаний МНН и комбинации с ним.
    # Пропущенное написание дало бы молчаливый `pass`.
    wort = {inn for inn in registry_inns() if "зверобо" in inn.lower()}

    passed = {inn for inn in wort if taking("serotonergic", inn) != "yes"}

    assert passed == HOMEOPATHIC_ST_JOHNS_WORT


def test_sedative_group_cites_the_leaflet() -> None:
    # Листок-вкладыш, раздел 2, «Другие препараты» называет цетиризин, зопиклон и золпидем.
    assert any(s.source == "aviandr-leaflet-2026" for s in GROUPS["sedative"].sources)


@pytest.mark.parametrize(
    "code",
    # Ревью #76: органическое поражение мозга кодируют и вне F00–F09.
    [
        *("G40.1", "F06.4", "G30.1", "G35", "G93.4", "I63.5", "I69.3", "S06.2", "T90.5"),
        *("C71.9", "G93.0", "G93.1", "G93.9", "G10", "G20", "G23.1", "G09", "B94.1"),
        *("G80.0", "C79.3", "Q00.0", "Q03.9", "Q04.3"),
    ],
)
def test_organic_brain_disease_codes_contraindicate(code: str) -> None:
    assert outcome("epilepsy-organic", code) == "yes"


@pytest.mark.parametrize(
    "code",
    # G93.2 — доброкачественная внутричерепная гипертензия; G93.3 — утомляемость после
    # вирусной болезни, ей часто кодируют постковидную астению. По коду это не противопоказание.
    ["F41.1", "G93.2", "G93.3"],
)
def test_organic_brain_disease_without_code_is_asked(code: str) -> None:
    # Кодами все случаи не покрыть: врач уточняет сам.
    assert outcome("epilepsy-organic", code) == "unknown"


@pytest.mark.parametrize("code", ["K70.3", "K74.6", "K76.6"])
def test_cirrhosis_contraindicates_without_child_pugh(code: str) -> None:
    # Цирроз — не ниже класса A по Чайлд-Пью.
    assert outcome("hepatic", code) == "yes"


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("I10", "yes"),
        ("I25.1", "yes"),
        ("I95.1", "yes"),
        # Врождённые пороки сердца (повторное ревью #76).
        ("Q21.1", "yes"),
        ("I83.9", "no"),
        ("I84.1", "no"),
        ("I80.2", "no"),
    ],
)
def test_cardiovascular_caution_skips_veins(code: str, expected: str) -> None:
    assert outcome("cardiovascular", code) == expected


def test_reactions_follow_table_1() -> None:
    # ОХЛП 4.8, таблица 1. Лабораторные показатели пациент не отмечает.
    by_id = {r.id: r for r in AVIANDR.adverse_reactions}

    assert len(by_id) == 21
    assert not by_id["ck-increase"].patient_reportable
    assert not by_id["hemoglobin-decrease"].patient_reportable
    assert {r for r, x in by_id.items() if x.level == "mandatory"} == {
        "muscle-stiffness",
        "trismus",
        "hyperhidrosis",
        "hemoglobin-decrease",
    }


def test_level_is_sourced_by_the_leaflet() -> None:
    # Листок-вкладыш, раздел 4: «Немедленно сообщите лечащему врачу» — четыре реакции
    # уровня «обязательно», остальные — в списке «Другие возможные нежелательные реакции».
    # Снижения аппетита в листке нет: уровень «желательно», как у всех вне первого списка.
    locators = {"mandatory": LEAFLET_NOW, "desirable": LEAFLET_OTHER}
    for reaction in AVIANDR.adverse_reactions:
        cited = {r.locator for r in reaction.sources if r.source == "aviandr-leaflet-2026"}
        expected = set() if reaction.id == "appetite-decrease" else {locators[reaction.level]}
        assert cited == expected, reaction.id


def test_reaction_actions_follow_the_leaflet() -> None:
    # Листок-вкладыш, раздел 4: «Оценив Ваше состояние, врач может изменить схему лечения,
    # решить прекратить лечение или снизить дозу». ▼ — дополнительный мониторинг, начало листка.
    actions = AVIANDR.reaction_actions

    assert actions is not None
    assert [(r.source, r.locator) for r in actions.sources] == [
        ("aviandr-leaflet-2026", "раздел 4, после списка «Немедленно сообщите лечащему врачу»")
    ]
    assert AVIANDR.additional_monitoring
