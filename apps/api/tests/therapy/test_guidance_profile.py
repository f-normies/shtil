"""Профиль справки по КР, ключ, выбор КР и вопрос модели без БД (спека #107, 4.2 и 5).

Выбор КР проверяется на настоящем манифесте `knowledge/`: коды КР сопоставил медик.
"""

from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

import clin_compass_knowledge
import pytest
from clin_compass_knowledge.loader import load_knowledge

from clin_compass.modules.patients.schemas import DiagnosisOut, PatientSummary
from clin_compass.modules.therapy.checks import Catalog
from clin_compass.modules.therapy.guidance import (
    GuidanceProfile,
    age_group,
    build_profile,
    context,
    guideline_ids,
    question,
)

KNOWLEDGE = load_knowledge(Path(clin_compass_knowledge.__file__).resolve().parents[2])
CATALOG = Catalog.from_knowledge(KNOWLEDGE)
AVIANDR = CATALOG.drugs["aviandr"]
EMPTY = Catalog(drugs={}, registries={}, sources=KNOWLEDGE.sources)
TODAY = date(2026, 10, 21)
NOW = datetime(2026, 10, 1, tzinfo=UTC)
DEPRESSION = "kr-depression-2024"
GAD = "kr-gad-2024"


def patient(
    birth_date: date | None = date(1992, 5, 1), sex: str | None = "female"
) -> PatientSummary:
    return PatientSummary.model_validate(
        {
            "id": uuid4(),
            "full_name": "Пациентка Выдуманная",
            "email": "patient@example.com",
            "lead_doctor_id": uuid4(),
            "created_at": NOW,
            "phone": "+79000000001",
            "birth_date": birth_date,
            "sex": sex,
        }
    )


def diagnosis(code: str, *, resolved: bool = False) -> DiagnosisOut:
    return DiagnosisOut(
        id=uuid4(),
        icd10=code,
        title=f"Диагноз {code}",
        set_by=uuid4(),
        set_at=NOW,
        resolved_at=NOW if resolved else None,
        resolved_by=uuid4() if resolved else None,
    )


def profile_of(*codes: str, catalog: Catalog = CATALOG, **fields: object) -> GuidanceProfile:
    found = build_profile(catalog, patient(**fields), [diagnosis(c) for c in codes], TODAY)  # type: ignore[arg-type]
    assert found is not None
    return found


def test_order_of_diagnoses_does_not_change_the_key() -> None:
    assert profile_of("F41.1", "F32.1").key == profile_of("F32.1", "F41.1").key


def test_resolved_diagnosis_is_not_in_the_profile() -> None:
    with_resolved = build_profile(
        CATALOG, patient(), [diagnosis("F41.1"), diagnosis("F32.1", resolved=True)], TODAY
    )

    assert with_resolved == profile_of("F41.1")
    assert with_resolved is not None and with_resolved.diagnoses == ("F41.1",)


def test_without_active_diagnoses_there_is_no_profile() -> None:
    assert build_profile(CATALOG, patient(), [diagnosis("F41.1", resolved=True)], TODAY) is None


def test_other_sex_gives_other_key() -> None:
    assert profile_of("F41.1", sex="male").key != profile_of("F41.1", sex="female").key


def test_key_is_sha256_of_the_profile_and_survives_storage() -> None:
    profile = profile_of("F41.1")

    assert len(profile.key) == 64
    assert GuidanceProfile.from_json(profile.to_json()) == profile


def test_profile_has_no_name_contacts_or_birth_date() -> None:
    stored = str(profile_of("F41.1").to_json())

    assert "Выдуманная" not in stored
    assert "example.com" not in stored
    assert "1992" not in stored
    assert "+7900" not in stored


@pytest.mark.parametrize(
    ("birth_date", "expected"),
    [
        # Границы групп — в день рождения.
        (date(2008, 10, 22), "<18"),
        (date(2008, 10, 21), "18–64"),
        (date(1961, 10, 22), "18–64"),
        (date(1961, 10, 21), "≥65"),
        (None, None),
    ],
)
def test_age_group_on_birthday(birth_date: date | None, expected: str | None) -> None:
    assert age_group(birth_date, TODAY) == expected


@pytest.mark.parametrize(
    ("codes", "expected"),
    [
        (["F41.1"], [GAD]),
        (["F32.1"], [DEPRESSION]),
        (["F41.1", "F32.1"], [DEPRESSION, GAD]),
        (["F33.0"], [DEPRESSION]),
        # Сценарий 2: тревога после COVID-19. КР по этим кодам в базе нет.
        (["F43.2", "U09.9"], []),
    ],
)
def test_guidelines_are_chosen_by_icd10(codes: list[str], expected: list[str]) -> None:
    assert guideline_ids(KNOWLEDGE.sources, codes) == expected


def test_source_ids_are_guidelines_and_catalog_labels() -> None:
    assert profile_of("F41.1").source_ids == (AVIANDR.label, GAD)
    assert profile_of("F43.2", "U09.9").source_ids == (AVIANDR.label,)
    assert profile_of("F43.2", catalog=EMPTY).source_ids == ()


def test_question_names_codes_guidelines_and_age_group() -> None:
    text = question(profile_of("F41.1", "F32.1", "F43.2"), KNOWLEDGE.sources)

    assert text == (
        "Какие варианты фармакотерапии и немедикаментозного лечения рекомендованы взрослому "
        "при F32.1 (Депрессивный эпизод, Рекуррентное депрессивное расстройство), "
        "F41.1 (Генерализованное тревожное расстройство), F43.2?"
    )


@pytest.mark.parametrize(
    ("birth_date", "whom"),
    [
        (date(1950, 1, 1), "пациенту 65 лет и старше"),
        (date(2010, 1, 1), "пациенту младше 18 лет"),
        (None, "пациенту"),
    ],
)
def test_question_names_the_age_group(birth_date: date | None, whom: str) -> None:
    text = question(profile_of("F41.1", birth_date=birth_date), KNOWLEDGE.sources)

    assert f"рекомендованы {whom} при F41.1" in text


@pytest.mark.parametrize(
    ("sex", "birth_date", "expected"),
    [
        ("female", date(1992, 5, 1), "Пол: женский.\nВозраст: 18–64 года."),
        ("male", date(1950, 1, 1), "Пол: мужской.\nВозраст: 65 лет и старше."),
        (None, None, "Пол: не указан.\nВозраст: не указан."),
    ],
)
def test_context_has_only_sex_and_age_group(
    sex: str | None, birth_date: date | None, expected: str
) -> None:
    assert context(profile_of("F41.1", sex=sex, birth_date=birth_date)) == expected
