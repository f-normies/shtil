"""Справка по КР при разном справочнике препаратов (спека #107, 4.2 и 4.4).

Справочник передаётся в сервис явно: так проверяется смена ключа без правки данных пациента.
"""

from datetime import date
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.modules.patients import service as patients
from clin_compass.modules.patients.schemas import DiagnosisIn, ProfileUpdate
from clin_compass.modules.therapy import service
from clin_compass.modules.therapy.checks import Catalog
from clin_compass.modules.therapy.schemas import GuidanceOut
from tests.freetext.conftest import make_care
from tests.therapy.conftest import KNOWLEDGE
from tests.therapy.guidance_helpers import SOURCES, jobs, rows

TODAY = date(2026, 10, 21)
EMPTY = Catalog(drugs={}, registries={}, sources=SOURCES)
CATALOG = Catalog.load(KNOWLEDGE)


async def _patient_with(maker: async_sessionmaker[AsyncSession], code: str) -> tuple[UUID, UUID]:
    care = await make_care(maker, "doctor@example.com", "patient@example.com")
    async with maker() as session:
        patient = await patients.get_patient_for_doctor(
            session, doctor_id=care.doctor.id, patient_id=care.patient.id
        )
        assert patient is not None
        await patients.set_profile(
            session, patient, ProfileUpdate(birth_date=date(1992, 5, 1), sex="female")
        )
        await patients.add_diagnosis(
            session, patient.id, DiagnosisIn(icd10=code, title="Диагноз"), doctor_id=care.doctor.id
        )
        await session.commit()
    return care.doctor.id, care.patient.id


async def _shown(
    maker: async_sessionmaker[AsyncSession], catalog: Catalog, doctor_id: UUID, patient_id: UUID
) -> GuidanceOut:
    """То, что делает `GET …/guidance` с этим справочником."""
    async with maker() as session:
        patient = await patients.get_patient_for_doctor(
            session, doctor_id=doctor_id, patient_id=patient_id
        )
        assert patient is not None
        shown = await service.get_guidance(session, catalog, patient=patient, today=TODAY)
        await session.commit()
    return shown


async def test_no_guideline_and_empty_catalog_give_empty_answer_without_job(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    doctor_id, patient_id = await _patient_with(sessionmaker, "F43.2")

    shown = await _shown(sessionmaker, EMPTY, doctor_id, patient_id)

    assert (shown.status, shown.stale, shown.claims) == ("done", False, [])
    assert (shown.guidelines, shown.catalog_drugs) == ([], [])
    assert shown.computed_at is not None
    [row] = await rows(sessionmaker)
    assert row["profile"]["source_ids"] == []
    assert row["answer"] == {"claims": [], "dropped": 0, "run_id": None}
    assert row["run_id"] is None
    assert await jobs(sessionmaker) == []


async def test_other_catalog_changes_the_key_and_get_requests_it(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    doctor_id, patient_id = await _patient_with(sessionmaker, "F43.2")
    await _shown(sessionmaker, EMPTY, doctor_id, patient_id)

    shown = await _shown(sessionmaker, CATALOG, doctor_id, patient_id)

    old, new = await rows(sessionmaker)
    assert (old["status"], new["status"]) == ("done", "pending")
    assert new["profile"]["source_ids"] == ["test-label"]
    assert await jobs(sessionmaker) == [
        (new["profile_key"], f"therapy.guidance:{new['profile_key']}", "queued")
    ]
    assert (shown.status, shown.stale, shown.catalog_drugs) == ("pending", True, ["Тестин"])
