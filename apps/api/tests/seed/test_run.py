"""`make seed` на настоящей БД (спека #19, раздел 6).

План — маленький, на тестовых шкалах и правиле из `tests/assessments/knowledge`: настоящие
GAD-7 и PHQ-9 ещё не в `main`. Правило `test-cross-scale` срабатывает, когда сумма
`test-eligible` ≥ 2 и пункт q3 `test-eligible-second` ≥ 1.
"""

from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import httpx
import pytest
from clin_compass_knowledge.loader import load_knowledge
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.modules.alerts import service as alerts
from clin_compass.modules.alerts.deps import RuleBook
from clin_compass.modules.assessments.service import ScaleCatalog
from clin_compass.modules.identity.schemas import Role
from clin_compass.modules.patients import service as patients
from clin_compass.modules.therapy.checks import Catalog
from clin_compass.seed.data import (
    Plan,
    Record,
    SeedCourse,
    SeedDiagnosis,
    SeedDoctor,
    SeedMedication,
    SeedPatient,
)
from clin_compass.seed.run import Report, SeedError, seed
from tests.assessments.conftest import KNOWLEDGE
from tests.conftest import MakeUser
from tests.therapy.conftest import KNOWLEDGE as DRUGS_KNOWLEDGE
from tests.therapy.guidance_helpers import finish_jobs, jobs, rows

KNOWN = load_knowledge(KNOWLEDGE)
CATALOG = ScaleCatalog.from_knowledge(KNOWN)
RULES = RuleBook(KNOWN.rules.values(), KNOWN.sources)
WITHOUT_SECOND = ScaleCatalog(
    scales={k: v for k, v in KNOWN.scales.items() if k != "test-eligible-second"},
    sources=KNOWN.sources,
)
PASSWORD = "seed password for tests"
# Справочник с КР по F41.1 и препаратом: сид ставит расчёт справки по КР (спека #107).
DRUGS = Catalog.load(DRUGS_KNOWLEDGE)
NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)

DOCTOR = SeedDoctor("seed-doctor@example.com", "Врач Первый")
OTHER = SeedDoctor("seed-doctor2@example.com", "Врач Второй")
PATIENT = SeedPatient(
    "seed-patient@example.com",
    "Пациент Первый",
    DOCTOR.email,
    "+79000000001",
    (
        Record("test-eligible", 10, (0, 1)),
        Record("test-eligible", 3, (1, 1)),
        Record("test-eligible-second", 3, (1,)),
    ),
    birth_date=date(1992, 3, 14),
    sex="female",
    diagnoses=(SeedDiagnosis("F41.1", "Генерализованное тревожное расстройство"),),
    medications=(SeedMedication("Аурорикс", stopped_days_ago=10),),
)
EMPTY = SeedPatient("seed-empty@example.com", "Пациент Второй", OTHER.email, None, ())
PLAN = Plan(doctors=(DOCTOR, OTHER), patients=(PATIENT, EMPTY))

COUNTS = """
SELECT (SELECT count(*) FROM identity.users), (SELECT count(*) FROM patients.patients),
       (SELECT count(*) FROM patients.invites), (SELECT count(*) FROM patients.consents),
       (SELECT count(*) FROM assessments.results), (SELECT count(*) FROM alerts.alerts),
       (SELECT count(*) FROM alerts.triggers), (SELECT count(*) FROM patients.diagnoses),
       (SELECT count(*) FROM therapy.medications)
"""


async def run(
    maker: async_sessionmaker[AsyncSession],
    *,
    catalog: ScaleCatalog = CATALOG,
    plan: Plan = PLAN,
    now: datetime = NOW,
    drugs: Catalog | None = None,
) -> Report:
    return await seed(
        maker, catalog=catalog, rules=RULES, plan=plan, password=PASSWORD, now=now, drugs=drugs
    )


async def counts(maker: async_sessionmaker[AsyncSession]) -> tuple[int, ...]:
    async with maker() as session:
        return tuple((await session.execute(text(COUNTS))).one())


async def user_id(maker: async_sessionmaker[AsyncSession], email: str) -> UUID:
    async with maker() as session:
        found = await session.scalar(
            text("SELECT id FROM identity.users WHERE email = :e"), {"e": email}
        )
    assert isinstance(found, UUID)
    return found


async def test_seed_creates_doctors_patients_and_history(
    sessionmaker: async_sessionmaker[AsyncSession], client: httpx.AsyncClient
) -> None:
    report = await run(sessionmaker)

    assert (report.doctors_created, report.patients_created, report.records_created) == (2, 2, 3)
    doctor_id = await user_id(sessionmaker, DOCTOR.email)
    async with sessionmaker() as session:
        [patient] = await patients.list_patients(session, doctor_id)
        consents = await session.scalar(text("SELECT count(*) FROM patients.consents"))
        results = (
            await session.execute(
                text(
                    "SELECT scale_id, completed_at FROM assessments.results "
                    "ORDER BY completed_at, scale_id"
                )
            )
        ).all()
    assert (patient.email, patient.full_name, patient.phone) == (
        PATIENT.email,
        PATIENT.full_name,
        PATIENT.phone,
    )
    assert consents == 2
    # 10 и 3 дня назад, 10:00 по Москве; начало наблюдения — за день до первой записи.
    ten_days = datetime(2026, 10, 8, 7, 0, tzinfo=UTC)
    three_days = datetime(2026, 10, 15, 7, 0, tzinfo=UTC)
    assert results == [
        ("test-eligible", ten_days),
        ("test-eligible", three_days),
        ("test-eligible-second", three_days),
    ]
    assert patient.created_at == ten_days - timedelta(days=1)
    for email in (DOCTOR.email, PATIENT.email):
        response = await client.post(
            "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
        )
        assert response.status_code == 200, email


async def test_signal_comes_from_the_rule(sessionmaker: async_sessionmaker[AsyncSession]) -> None:
    await run(sessionmaker)

    patient_id = await user_id(sessionmaker, PATIENT.email)
    async with sessionmaker() as session:
        signals = await alerts.list_for_patient(session, patient_id=patient_id)
    assert [(s.rule_id, s.origin_type) for s in signals] == [("test-cross-scale", "rule")]


async def test_second_run_changes_nothing(sessionmaker: async_sessionmaker[AsyncSession]) -> None:
    await run(sessionmaker)
    before = await counts(sessionmaker)

    report = await run(sessionmaker)

    assert await counts(sessionmaker) == before
    assert (report.doctors_created, report.patients_created, report.records_created) == (0, 0, 0)
    assert (report.doctors_existing, report.patients_existing, report.records_existing) == (2, 2, 3)


async def test_run_on_another_day_does_not_duplicate(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await run(sessionmaker)
    before = await counts(sessionmaker)

    report = await run(sessionmaker, now=NOW + timedelta(days=3))

    assert await counts(sessionmaker) == before
    # Ключ тот же, дата другая: запись в базе осталась от первого запуска.
    assert (report.records_existing, report.records_conflict) == (0, 3)


async def test_changed_answers_are_counted_as_conflict(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await run(sessionmaker)
    before = await counts(sessionmaker)
    first, *rest = PATIENT.records
    changed = SeedPatient(
        PATIENT.email,
        PATIENT.full_name,
        PATIENT.doctor_email,
        PATIENT.phone,
        (Record(first.scale_id, first.days_ago, (1, 1)), *rest),
    )

    report = await run(sessionmaker, plan=Plan(doctors=PLAN.doctors, patients=(changed, EMPTY)))

    assert await counts(sessionmaker) == before
    assert (report.records_created, report.records_existing, report.records_conflict) == (0, 2, 1)


async def test_unavailable_scale_is_skipped_and_added_later(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    first = await run(sessionmaker, catalog=WITHOUT_SECOND)

    assert first.records_created == 2
    assert first.skipped == {"test-eligible-second": "нет в knowledge/"}
    assert await counts(sessionmaker) == (4, 2, 2, 2, 2, 0, 0, 1, 1)

    second = await run(sessionmaker)

    assert (second.records_created, second.records_existing, second.skipped) == (1, 2, {})
    # Последняя запись дописана через обычную отправку: правило сработало.
    assert await counts(sessionmaker) == (4, 2, 2, 2, 3, 1, 1, 1, 1)


async def test_existing_user_with_other_role_stops_seed(
    sessionmaker: async_sessionmaker[AsyncSession], make_user: MakeUser
) -> None:
    await make_user(email=DOCTOR.email, role=Role.PATIENT)

    with pytest.raises(SeedError, match=DOCTOR.email):
        await run(sessionmaker)


async def test_patient_of_another_doctor_stops_seed(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await run(sessionmaker)
    moved = SeedPatient(PATIENT.email, PATIENT.full_name, OTHER.email, None, ())

    with pytest.raises(SeedError, match=PATIENT.email):
        await run(sessionmaker, plan=Plan(doctors=PLAN.doctors, patients=(moved,)))


async def test_seed_sets_profile_and_diagnoses_once(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await run(sessionmaker)
    await run(sessionmaker)

    doctor_id = await user_id(sessionmaker, DOCTOR.email)
    async with sessionmaker() as session:
        [patient] = await patients.list_patients(session, doctor_id)
        diagnoses = await patients.list_diagnoses(session, patient.id)
    assert (patient.birth_date, patient.sex) == (date(1992, 3, 14), "female")
    assert [(d.icd10, d.set_by) for d in diagnoses] == [("F41.1", doctor_id)]


async def test_seed_adds_medications_once_with_stop_dates(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await run(sessionmaker)
    await run(sessionmaker)

    async with sessionmaker() as session:
        rows = (
            await session.execute(text("SELECT name, stopped_on FROM therapy.medications"))
        ).all()
    assert rows == [("Аурорикс", date(2026, 10, 8))]


async def test_seed_requests_guidance_for_patients_with_diagnoses_once(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await run(sessionmaker, drugs=DRUGS)
    await run(sessionmaker, drugs=DRUGS)

    [row] = await rows(sessionmaker)
    assert row["patient_id"] == await user_id(sessionmaker, PATIENT.email)
    assert row["status"] == "pending"
    assert row["profile"]["diagnoses"] == ["F41.1"]
    assert [key for key, _, _ in await jobs(sessionmaker)] == [row["profile_key"]]


async def test_seed_without_drug_catalog_does_not_request_guidance(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    # Без справочника нет ни КР, ни ОХЛП: пустой «готовый» ответ сид не создаёт.
    await run(sessionmaker)

    assert await rows(sessionmaker) == []
    assert await jobs(sessionmaker) == []


async def test_repeated_seed_returns_failed_guidance_to_pending(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await run(sessionmaker, drugs=DRUGS)
    await finish_jobs(sessionmaker)
    async with sessionmaker() as session:
        await session.execute(text("UPDATE therapy.guidance SET status = 'failed', failures = 3"))
        await session.commit()

    await run(sessionmaker, drugs=DRUGS)

    [row] = await rows(sessionmaker)
    assert (row["status"], row["failures"]) == ("pending", 0)
    assert [key for key, _, _ in await jobs(sessionmaker)] == [row["profile_key"]]


TREATED = SeedPatient(
    "seed-treated@example.com",
    "Пациент Третий",
    DOCTOR.email,
    None,
    (),
    birth_date=date(1985, 7, 22),
    sex="female",
    diagnoses=(SeedDiagnosis("F41.1", "Генерализованное тревожное расстройство"),),
    course=SeedCourse(days_ago=5, missed_evenings=(3,), unmarked_mornings=(2,)),
)


async def test_seed_prescribes_and_marks_doses_once(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    # Спека #34, раздел 8: назначение через подбор и подтверждение врачом, отметки — один раз.
    plan = Plan(doctors=(DOCTOR,), patients=(TREATED,))
    await run(sessionmaker, plan=plan, drugs=DRUGS)
    await run(sessionmaker, plan=plan, drugs=DRUGS)

    async with sessionmaker() as session:
        [(started_at, confirmed)] = (
            await session.execute(
                text(
                    "SELECT p.started_at, r.status FROM therapy.prescriptions p"
                    " JOIN proposals.proposals r ON r.id = p.proposal_id"
                )
            )
        ).all()
        marks = (
            await session.execute(
                text("SELECT status, count(*) FROM adherence.dose_marks GROUP BY status")
            )
        ).all()
    assert started_at == NOW - timedelta(days=5)
    assert confirmed == "confirmed"
    # 6 дней по 2 дозы: без вечера сегодня и утра 2 дня назад; вечер 3 дня назад пропущен.
    assert dict(marks) == {"taken": 9, "missed": 1}
