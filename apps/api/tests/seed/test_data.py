"""Данные сида: ответы подходят к шкалам, пациентка 1 повторяет сценарий 1 (спека #19, раздел 6).

Шкалы и сценарий ещё не в `main` (PR #61, #64), поэтому ожидаемые числа записаны здесь
со ссылкой на источник, а не читаются из `knowledge/`. Исключение — сигналы «обязательно»:
их проверяют настоящие правила из `knowledge/`, чтобы тест не расходился с порогами.
"""

from collections.abc import Iterator
from datetime import date

import pytest
from clin_compass_knowledge.loader import load_knowledge
from clin_compass_knowledge.rules import evaluate
from clin_compass_knowledge.schemas.rule import Results
from clin_compass_knowledge.scoring import score

from clin_compass.modules.alerts.deps import repository_knowledge_dir
from clin_compass.modules.patients.schemas import DiagnosisIn, PhoneUpdate
from clin_compass.modules.therapy.checks import age_on
from clin_compass.seed.data import PLAN, Record, SeedPatient

# Число пунктов — определения шкал из PR #61: GAD-7 q1…q7, PHQ-9 q1…q9.
ITEMS = {"gad-7": 7, "phq-9": 9}
SCENARIO_PATIENT = "patient1@example.com"
DEMO_DOCTOR = "doctor@example.com"

RECORDS = [(p.email, r) for p in PLAN.patients for r in p.records]
# knowledge/ репозитория, как у `make seed` без COMPASS_KNOWLEDGE_DIR.
REPOSITORY = load_knowledge(repository_knowledge_dir())


def phq9_item9(record: Record) -> int:
    return record.answers[8]


@pytest.mark.parametrize(("email", "record"), RECORDS)
def test_answers_fit_the_scale(email: str, record: Record) -> None:
    assert len(record.answers) == ITEMS[record.scale_id], email
    assert all(0 <= a <= 3 for a in record.answers), email


@pytest.mark.parametrize(("email", "record"), RECORDS)
def test_records_are_in_the_past(email: str, record: Record) -> None:
    # Время заполнения — 10:00 по Москве: запись «сегодня» могла бы оказаться в будущем.
    assert record.days_ago >= 1, email


def test_scenario_patient_follows_steps_s2_and_s7() -> None:
    # knowledge/scenarios/scenario-1-aviandr.yaml (PR #64): s2 — GAD-7 14, PHQ-9 8 (пункт 9 — 0);
    # s7 через 2 недели — GAD-7 9, PHQ-9 6 (пункт 9 — 0).
    patient = next(p for p in PLAN.patients if p.email == SCENARIO_PATIENT)
    steps = sorted(patient.records, key=lambda r: (-r.days_ago, r.scale_id))

    assert [(r.scale_id, sum(r.answers)) for r in steps] == [
        ("gad-7", 14),
        ("phq-9", 8),
        ("gad-7", 9),
        ("phq-9", 6),
    ]
    assert steps[0].days_ago - steps[2].days_ago == 14
    assert [phq9_item9(r) for r in steps if r.scale_id == "phq-9"] == [0, 0]


def results_after_each_record(patient: SeedPatient) -> Iterator[Results]:
    """Последние ответы и баллы по каждой шкале после каждой отправки сида, как их видят правила.

    Шкалы берутся и черновые: когда их проверят, записи попадут в базу.
    """
    latest: Results = {}
    for record in sorted(patient.records, key=lambda r: -r.days_ago):
        scale = REPOSITORY.scales.get(record.scale_id)
        if scale is None:
            continue
        answers = dict(zip((item.id for item in scale.items), record.answers, strict=True))
        values = {key: float(value) for key, value in answers.items()}
        values |= {key: float(result.value) for key, result in score(scale, answers).items()}
        latest[scale.id] = values
        yield dict(latest)


def test_demo_doctor_has_no_answer_raising_a_mandatory_signal() -> None:
    # Шаг s8 сценария 1 выполняется в демо вживую: до него у врача демо не должно быть сигналов
    # «обязательно». Правила исполняются настоящие, из knowledge/, и черновики тоже: сигналы
    # появятся, когда правила проверят.
    demo = [p for p in PLAN.patients if p.doctor_email == DEMO_DOCTOR]
    mandatory = [r for r in REPOSITORY.rules.values() if r.level == "mandatory"]
    assert SCENARIO_PATIENT in {p.email for p in demo}
    if not mandatory:
        pytest.skip("в knowledge/ нет правил «обязательно»")

    fired = {
        (patient.email, rule.id)
        for patient in demo
        for results in results_after_each_record(patient)
        for rule in mandatory
        if evaluate(rule, results)
    }

    assert fired == set()


def test_someone_else_has_a_mandatory_signal_for_screenshots() -> None:
    others = [p for p in PLAN.patients if p.doctor_email != DEMO_DOCTOR]

    assert any(phq9_item9(r) >= 1 for p in others for r in p.records if r.scale_id == "phq-9")


def test_people_are_unique_and_fictional() -> None:
    emails = [d.email for d in PLAN.doctors] + [p.email for p in PLAN.patients]
    phones = [p.phone for p in PLAN.patients if p.phone is not None]

    assert len(set(emails)) == len(emails)
    assert all(e.endswith("@example.com") for e in emails)
    assert len(set(phones)) == len(phones)
    assert all(PhoneUpdate(phone=p).phone == p for p in phones)


def test_phones_cannot_reach_a_real_person() -> None:
    # Карточка врача показывает номер ссылкой tel:. Российский код зоны не начинается с 0
    # (план нумерации: 3xx, 4xx, 8xx, 9xx), поэтому номер +7 0… не существует.
    phones = [p.phone for p in PLAN.patients if p.phone is not None]

    assert phones
    assert all(p.startswith("+70") for p in phones)


@pytest.mark.parametrize("patient", PLAN.patients, ids=lambda p: p.email)
def test_record_keys_are_unique(patient: SeedPatient) -> None:
    # Ключ отправки строится из email, шкалы и дня: две записи одной шкалы в один день
    # получили бы один ключ, и вторая молча не записалась бы.
    keys = [(r.scale_id, r.days_ago) for r in patient.records]

    assert len(set(keys)) == len(keys)


def test_every_patient_has_a_known_doctor() -> None:
    doctors = {d.email for d in PLAN.doctors}

    assert all(p.doctor_email in doctors for p in PLAN.patients)


def test_scenario_patient_has_the_profile_of_scenario_1() -> None:
    # Сценарий 1: женщина 34 лет (профиль), предварительный диагноз F41.1 (шаг s4).
    patient = next(p for p in PLAN.patients if p.email == SCENARIO_PATIENT)

    assert patient.sex == "female"
    assert age_on(patient.birth_date, date(2026, 10, 28)) == 34  # type: ignore[arg-type]
    assert [d.icd10 for d in patient.diagnoses] == ["F41.1"]


def test_diagnoses_are_valid_codes() -> None:
    for patient in PLAN.patients:
        for diagnosis in patient.diagnoses:
            DiagnosisIn(icd10=diagnosis.icd10, title=diagnosis.title)


def test_scenario_3_patients_stopped_moclobemide_10_and_16_days_ago() -> None:
    # Сценарий 3: моклобемид отменён 10 дней назад (шаг s5) и 16 дней назад (шаги s8–s9).
    stops = sorted(
        m.stopped_days_ago for p in PLAN.patients for m in p.medications if m.name == "Аурорикс"
    )

    assert stops == [10, 16]


SCENARIO_2_PATIENT = "patient8@example.com"


def test_scenario_2_patient_has_the_profile_and_step_s2() -> None:
    # knowledge/scenarios/scenario-2-aviandr-post-covid.yaml: мужчина 46 лет (профиль),
    # суматриптан по требованию (анамнез), GAD-7 9 и PHQ-9 6 с пунктом 9 — 0 (шаг s2).
    # Диагнозы — показание «Авиандра» post-covid-anxiety: F43.2 и U09.9
    # (knowledge/drugs/aviandr.yaml, icd10 и requires_any).
    patient = next(p for p in PLAN.patients if p.email == SCENARIO_2_PATIENT)

    assert patient.doctor_email == DEMO_DOCTOR
    assert patient.sex == "male"
    assert age_on(patient.birth_date, date(2026, 10, 30)) == 46  # type: ignore[arg-type]
    assert sorted(d.icd10 for d in patient.diagnoses) == ["F43.2", "U09.9"]
    assert [(m.name, m.stopped_days_ago) for m in patient.medications] == [("Суматриптан", None)]
    assert sorted((r.scale_id, sum(r.answers)) for r in patient.records) == [
        ("gad-7", 9),
        ("phq-9", 6),
    ]
    assert [phq9_item9(r) for r in patient.records if r.scale_id == "phq-9"] == [0]
