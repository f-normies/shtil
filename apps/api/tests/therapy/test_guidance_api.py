"""Запрос расчёта справки по КР и ручки на настоящей БД, без worker'а (спека #107, 4.4–4.5).

Справочник — `tests/therapy/knowledge`: КР `test-guideline` (F41.1) и `test-drug` с ОХЛП
`test-label`. Задачу справки здесь никто не выполняет: она остаётся в очереди.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.conftest import MakeUser
from tests.patients.test_access import login
from tests.therapy.conftest import PATIENT, Care
from tests.therapy.guidance_helpers import (
    ENTRYPOINT,
    GUIDELINE_TITLE,
    finish_jobs,
    guidance,
    jobs,
    rows,
)
from tests.therapy.test_api import add_diagnosis, set_profile

POST_COVID = {"icd10": "F43.2", "title": "Расстройство адаптации"}


async def test_new_diagnosis_creates_pending_row_and_job(
    care: Care, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    await set_profile(care)
    await add_diagnosis(care)

    [row] = await rows(sessionmaker)
    key = row["profile_key"]
    assert (row["patient_id"], row["status"], row["failures"]) == (care.patient_id, "pending", 0)
    assert row["profile"] == {
        "diagnoses": ["F41.1"],
        "sex": "female",
        "age_group": "18–64",
        "source_ids": ["test-guideline", "test-label"],
    }
    assert (row["answer"], row["run_id"], row["computed_at"]) == (None, None, None)
    assert await jobs(sessionmaker) == [(key, f"{ENTRYPOINT}:{key}", "queued")]
    shown = await guidance(care)
    assert shown == {
        "status": "pending",
        "stale": False,
        "claims": [],
        "computed_at": None,
        "guidelines": [{"id": "test-guideline", "title": GUIDELINE_TITLE}],
        "catalog_drugs": ["Тестин"],
        # Ответа ещё нет: профиля показанного ответа тоже нет.
        "profile": None,
    }


async def test_egfr_and_outside_medication_do_not_change_the_key(
    care: Care, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    await set_profile(care)
    await add_diagnosis(care)

    egfr = await care.client.put(
        f"{care.base}/profile",
        json={"birth_date": "1992-05-01", "sex": "female", "egfr": 50, "egfr_measured_on": None},
    )
    medication = await care.client.post(f"{care.base}/medications", json={"name": "Аурорикс"})

    assert (egfr.status_code, medication.status_code) == (200, 201)
    assert len(await rows(sessionmaker)) == 1
    assert len(await jobs(sessionmaker)) == 1


async def test_profile_change_requests_new_key(
    care: Care, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    await set_profile(care)
    await add_diagnosis(care)

    await set_profile(care, sex="male")

    first, second = await rows(sessionmaker)
    assert second["profile"]["sex"] == "male"
    assert first["profile_key"] != second["profile_key"]
    assert [key for key, _, _ in await jobs(sessionmaker)] == [
        first["profile_key"],
        second["profile_key"],
    ]


async def test_without_active_diagnoses_status_is_null_and_nothing_is_queued(
    care: Care, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    await set_profile(care)

    shown = await guidance(care)

    assert (shown["status"], shown["stale"], shown["claims"]) == (None, False, [])
    assert (shown["guidelines"], shown["catalog_drugs"]) == ([], ["Тестин"])
    assert shown["profile"] is None
    assert await rows(sessionmaker) == []
    assert await jobs(sessionmaker) == []


async def test_get_requeues_pending_row_without_job_once(
    care: Care, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    await set_profile(care)
    await add_diagnosis(care)
    [row] = await rows(sessionmaker)
    await finish_jobs(sessionmaker)

    await guidance(care)
    await guidance(care)

    assert await jobs(sessionmaker) == [
        (row["profile_key"], f"{ENTRYPOINT}:{row['profile_key']}", "queued")
    ]
    assert len(await rows(sessionmaker)) == 1


async def test_without_guideline_only_the_catalog_label_is_asked(
    care: Care, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    await set_profile(care)
    await add_diagnosis(care, POST_COVID)

    [row] = await rows(sessionmaker)
    shown = await guidance(care)

    assert row["profile"]["source_ids"] == ["test-label"]
    assert len(await jobs(sessionmaker)) == 1
    assert (shown["status"], shown["guidelines"]) == ("pending", [])


async def test_retry_returns_failed_row_to_pending_with_a_new_job(
    care: Care, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    await set_profile(care)
    await add_diagnosis(care)
    await finish_jobs(sessionmaker)
    async with sessionmaker() as session:
        await session.execute(text("UPDATE therapy.guidance SET status = 'failed', failures = 3"))
        await session.commit()
    assert (await guidance(care))["status"] == "failed"

    retried = await care.client.post(f"{care.base}/therapy/guidance/retry")
    again = await care.client.post(f"{care.base}/therapy/guidance/retry")

    assert retried.status_code == 200, retried.text
    assert retried.json()["status"] == "pending"
    assert again.status_code == 409
    [row] = await rows(sessionmaker)
    assert (row["status"], row["failures"]) == ("pending", 0)
    assert len(await jobs(sessionmaker)) == 1


async def test_retry_without_diagnoses_is_a_conflict(care: Care) -> None:
    response = await care.client.post(f"{care.base}/therapy/guidance/retry")

    assert response.status_code == 409


async def test_other_doctor_and_patient_are_denied_and_logged(
    care: Care, make_user: MakeUser, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    await set_profile(care)
    await add_diagnosis(care)
    await guidance(care)
    await make_user(email="other@example.com")
    care.client.cookies.clear()
    await login(care.client, "other@example.com")

    other = [
        await care.client.get(f"{care.base}/therapy/guidance"),
        await care.client.post(f"{care.base}/therapy/guidance/retry"),
    ]
    care.client.cookies.clear()
    await login(care.client, PATIENT)
    patient = [
        await care.client.get(f"{care.base}/therapy/guidance"),
        await care.client.post(f"{care.base}/therapy/guidance/retry"),
    ]

    assert [r.status_code for r in other] == [404, 404]
    assert [r.status_code for r in patient] == [403, 403]
    async with sessionmaker() as session:
        actions = (
            await session.scalars(
                text("SELECT action FROM identity.access_log WHERE action LIKE '%guidance%'")
            )
        ).all()
    assert sorted(actions) == [
        "patient.therapy.guidance",
        "patient.therapy.guidance.denied",
        "patient.therapy.guidance.retry.denied",
    ]
