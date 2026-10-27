"""Отметка нежелательной реакции и черновик извещения на настоящей БД (спека #29, раздел 8).

Справочник — `tests/therapy/knowledge`: у `test-drug` «скованность мышц» уровня
«обязательно», «головная боль» — «желательно», «повышение КФК» пациент не отмечает.
"""

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.conftest import MakeUser
from tests.patients.test_access import login, register_patient
from tests.therapy.conftest import DOCTOR, PATIENT, Care
from tests.therapy.test_safety_api import add_medication, days_ago, prescribed, safety_alerts


async def as_patient(care: Care, email: str = PATIENT) -> None:
    care.client.cookies.clear()
    await login(care.client, email)


async def as_doctor(care: Care) -> None:
    care.client.cookies.clear()
    await login(care.client, DOCTOR)


async def report(care: Care, prescription_id: str, **body: Any) -> Any:
    return await care.client.post(
        f"/api/v1/me/prescriptions/{prescription_id}/reactions", json=body
    )


async def stop(care: Care, prescription_id: str) -> None:
    response = await care.client.post(
        f"/api/v1/prescriptions/{prescription_id}/stop", json={"reason": "Нежелательная реакция"}
    )
    assert response.status_code == 200, response.text


async def test_patient_sees_reactions_to_report_in_their_schedule(care: Care) -> None:
    await prescribed(care)
    await as_patient(care)

    [item] = (await care.client.get("/api/v1/me/prescriptions")).json()

    assert item["reactions"] == [
        {"id": "stiffness", "title": "скованность мышц"},
        {"id": "headache", "title": "болит голова"},
    ]


async def test_reported_reaction_raises_a_mandatory_safety_signal(care: Care) -> None:
    prescription = await prescribed(care)
    await as_patient(care)

    response = await report(
        care, prescription["id"], reactions=["headache", "stiffness"], note="  сводит челюсть  "
    )
    await as_doctor(care)
    [signal] = await safety_alerts(care)
    reports = (await care.client.get(f"{care.base}/reactions")).json()

    assert response.status_code == 201, response.text
    assert (signal["level"], signal["title"]) == (
        "mandatory",
        "Нежелательная реакция на «Тестин»: головная боль, скованность мышц",
    )
    assert signal["doctor_actions"] == ["Оценить состояние пациента"]
    assert [s["locator"] for s in signal["sources"]] == ["4.8"]
    evidence = signal["triggers"][0]["evidence"]
    assert evidence["prescription_id"] == prescription["id"]
    # Вид сигнала безопасности: по нему #95 выбирает варианты решения.
    assert evidence["safety_kind"] == "reaction"
    assert evidence["detail"].endswith("Пациент пишет: «сводит челюсть»")
    [saved] = reports
    assert (saved["prescription_id"], saved["note"]) == (prescription["id"], "сводит челюсть")
    assert [(r["id"], r["level"]) for r in saved["reactions"]] == [
        ("headache", "desirable"),
        ("stiffness", "mandatory"),
    ]


async def test_each_report_gets_its_own_signal(care: Care) -> None:
    prescription = await prescribed(care)
    await as_patient(care)

    await report(care, prescription["id"], reactions=["headache"])
    await report(care, prescription["id"], reactions=["stiffness"])
    await as_doctor(care)
    signals = await safety_alerts(care)

    assert sorted(s["level"] for s in signals) == ["desirable", "mandatory"]


async def test_reaction_outside_the_list_is_rejected(care: Care) -> None:
    prescription = await prescribed(care)
    await as_patient(care)

    unknown = await report(care, prescription["id"], reactions=["no-such-reaction"])
    lab = await report(care, prescription["id"], reactions=["ck-up"])
    empty = await report(care, prescription["id"], reactions=[])
    await as_doctor(care)

    assert [unknown.status_code, lab.status_code, empty.status_code] == [422, 422, 422]
    assert await safety_alerts(care) == []


async def test_stopped_prescription_takes_no_reports(care: Care) -> None:
    prescription = await prescribed(care)
    await stop(care, prescription["id"])
    await as_patient(care)

    response = await report(care, prescription["id"], reactions=["headache"])

    assert response.status_code == 409


async def test_other_patient_and_doctor_cannot_report_or_read(
    care: Care, make_user: MakeUser, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    prescription = await prescribed(care)
    await register_patient(care.client, DOCTOR, "other-patient@example.com")
    await as_patient(care, "other-patient@example.com")
    foreign = await report(care, prescription["id"], reactions=["headache"])
    await as_doctor(care)
    doctor = await report(care, prescription["id"], reactions=["headache"])
    await make_user(email="other-doctor@example.com")
    care.client.cookies.clear()
    await login(care.client, "other-doctor@example.com")
    listed = await care.client.get(f"{care.base}/reactions")
    drafted = await care.client.get(
        f"/api/v1/prescriptions/{prescription['id']}/adverse-event-draft"
    )

    assert [foreign.status_code, doctor.status_code] == [404, 403]
    assert [listed.status_code, drafted.status_code] == [404, 404]
    async with sessionmaker() as session:
        denied = (
            await session.scalars(
                text("SELECT action FROM identity.access_log WHERE action LIKE '%.denied'")
            )
        ).all()
    assert sorted(denied) == [
        "patient.reactions.list.denied",
        "prescriptions.reaction.denied",
        "therapy.adverse_event_draft.denied",
    ]


async def test_draft_of_a_stopped_prescription(
    care: Care, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    prescription = await prescribed(care)
    await add_medication(care, "Суматриптан", started_on=days_ago(30))
    await as_patient(care)
    await report(care, prescription["id"], reactions=["stiffness"], note="сводит челюсть")
    await as_doctor(care)
    await stop(care, prescription["id"])

    response = await care.client.get(
        f"/api/v1/prescriptions/{prescription['id']}/adverse-event-draft"
    )

    assert response.status_code == 200, response.text
    lines = response.json()["text"].splitlines()
    assert "Подозреваемый препарат: Тестин (тестин), РУ ЛП-000100" in lines
    assert any(line.startswith("Пациент: женский пол, ") for line in lines)
    assert any(line.startswith("Отмена: ") and "Нежелательная реакция" in line for line in lines)
    assert any(
        line.endswith("скованность мышц. Со слов пациента: «сводит челюсть»") for line in lines
    )
    assert any(line.startswith("- Суматриптан") for line in lines)
    async with sessionmaker() as session:
        actions = set((await session.scalars(text("SELECT action FROM identity.access_log"))).all())
    assert {"prescriptions.reaction", "therapy.adverse_event_draft"} <= actions


async def test_draft_needs_a_report(care: Care) -> None:
    prescription = await prescribed(care)

    response = await care.client.get(
        f"/api/v1/prescriptions/{prescription['id']}/adverse-event-draft"
    )

    assert response.status_code == 409
