"""Профиль, подбор, подтверждение и отмена назначения на настоящей БД (спека #28, раздел 10).

Справочник — `tests/therapy/knowledge`: `test-drug` (F41.1; курс по F43.2 + U09.9, 28–42 дня)
и черновик `test-draft`, которого в подборе быть не должно.
"""

import asyncio
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.conftest import MakeUser
from tests.patients.test_access import login
from tests.therapy.conftest import PATIENT, Care

GAD = {"icd10": "F41.1", "title": "Генерализованное тревожное расстройство"}


async def set_profile(care: Care, birth_date: str = "1992-05-01", sex: str = "female") -> None:
    response = await care.client.put(
        f"{care.base}/profile", json={"birth_date": birth_date, "sex": sex}
    )
    assert response.status_code == 200, response.text


async def add_diagnosis(care: Care, diagnosis: dict[str, str] = GAD) -> dict[str, Any]:
    response = await care.client.post(f"{care.base}/diagnoses", json=diagnosis)
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


async def options(care: Care) -> dict[str, Any]:
    response = await care.client.post(f"{care.base}/therapy/options")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


async def ready(care: Care) -> dict[str, Any]:
    """Профиль и F41.1 внесены, подбор выполнен: единственный вариант с предложением."""
    await set_profile(care)
    await add_diagnosis(care)
    [option] = (await options(care))["options"]
    return option  # type: ignore[no-any-return]


async def prescriptions(care: Care) -> list[dict[str, Any]]:
    response = await care.client.get(f"{care.base}/prescriptions")
    assert response.status_code == 200
    body: list[dict[str, Any]] = response.json()
    return body


async def test_profile_and_diagnoses_are_shown_and_resolving_keeps_the_row(care: Care) -> None:
    await set_profile(care)
    added = await add_diagnosis(care, {"icd10": "f41.1 ", "title": "ГТР"})

    resolved = await care.client.post(f"{care.base}/diagnoses/{added['id']}/resolve")
    listed = (await care.client.get(f"{care.base}/diagnoses")).json()
    summary = (await care.client.get(care.base)).json()

    assert added["icd10"] == "F41.1"
    assert resolved.status_code == 200
    assert [(d["icd10"], d["resolved_at"] is not None) for d in listed] == [("F41.1", True)]
    assert (summary["birth_date"], summary["sex"]) == ("1992-05-01", "female")


async def test_invalid_diagnosis_code_is_rejected(care: Care) -> None:
    response = await care.client.post(
        f"{care.base}/diagnoses", json={"icd10": "F41,1", "title": "x"}
    )

    assert response.status_code == 422


async def test_options_create_one_proposal_with_checks_and_sources(care: Care) -> None:
    option = await ready(care)

    assert (option["drug_id"], option["indication_id"]) == ("test-drug", "anxiety")
    assert {c["id"]: c["status"] for c in option["checks"]} == {
        "registered": "pass",
        "not_prescribed": "pass",
        "indication": "pass",
        "age": "pass",
        # Справочник #29: СКФ в профиле не указана — врачу нужно уточнить.
        "contra:maoi": "pass",
        "contra:renal": "manual",
        "caution:serotonergic": "pass",
    }
    assert all(c["source"]["title"] for c in option["checks"] if c["id"] != "not_prescribed")
    proposal = option["proposal"]
    assert (proposal["kind"], proposal["status"], proposal["origin_type"]) == (
        "therapy.prescription",
        "proposed",
        "rule",
    )
    assert proposal["payload"]["regimen"]["dose_mg"] == 10
    assert proposal["payload"]["instructions"][0]["text"] == "Внутрь до еды"


async def test_repeated_options_return_the_same_proposal(care: Care) -> None:
    first = await ready(care)

    again = await options(care)

    assert again["options"][0]["proposal"]["id"] == first["proposal"]["id"]


async def test_parallel_first_options_create_one_proposal(care: Care) -> None:
    await set_profile(care)
    await add_diagnosis(care)

    results = await asyncio.gather(*(options(care) for _ in range(4)))

    assert len({r["options"][0]["proposal"]["id"] for r in results}) == 1


async def test_resolved_diagnosis_is_not_counted_and_rejects_proposals(care: Care) -> None:
    option = await ready(care)
    diagnosis_id = (await care.client.get(f"{care.base}/diagnoses")).json()[0]["id"]

    await care.client.post(f"{care.base}/diagnoses/{diagnosis_id}/resolve")
    after = await options(care)
    proposals = (await care.client.get(f"{care.base}/proposals")).json()

    assert after["options"] == []
    [proposal] = [p for p in proposals if p["id"] == option["proposal"]["id"]]
    assert proposal["status"] == "rejected"


async def test_confirming_one_indication_closes_the_other(care: Care) -> None:
    # Один препарат по двум показаниям: после назначения второе предложение закрывается,
    # иначе его можно подтвердить позже по устаревшим проверкам.
    await set_profile(care)
    await add_diagnosis(care)
    await add_diagnosis(care, {"icd10": "F43.2", "title": "Расстройство адаптации"})
    await add_diagnosis(care, {"icd10": "U09.9", "title": "Состояние после COVID-19"})
    first, second = (await options(care))["options"]

    await care.client.post(f"/api/v1/proposals/{first['proposal']['id']}/confirm", json={})
    proposals = {p["id"]: p for p in (await care.client.get(f"{care.base}/proposals")).json()}

    closed = proposals[second["proposal"]["id"]]
    assert (closed["status"], closed["decision_comment"]) == (
        "rejected",
        "назначен по другому показанию",
    )


async def test_failed_check_gives_no_proposal(care: Care) -> None:
    await set_profile(care, birth_date="2012-01-01")
    await add_diagnosis(care)

    [option] = (await options(care))["options"]

    assert {c["id"]: c["status"] for c in option["checks"]}["age"] == "fail"
    assert option["proposal"] is None


async def test_no_matching_drug_is_explained(care: Care) -> None:
    await add_diagnosis(care, {"icd10": "F32.1", "title": "Депрессивный эпизод"})

    body = await options(care)

    assert body["options"] == []
    assert body["message"] == "В справочнике нет препарата по диагнозам пациента"


async def test_confirming_creates_a_prescription_the_patient_sees(
    care: Care, make_user: MakeUser
) -> None:
    option = await ready(care)

    confirmed = await care.client.post(
        f"/api/v1/proposals/{option['proposal']['id']}/confirm", json={}
    )
    [prescription] = await prescriptions(care)
    again = await options(care)
    care.client.cookies.clear()
    await login(care.client, PATIENT)
    schedule = (await care.client.get("/api/v1/me/prescriptions")).json()

    assert confirmed.status_code == 200
    assert (prescription["drug_id"], prescription["status"], prescription["off_label"]) == (
        "test-drug",
        "active",
        False,
    )
    assert prescription["card_snapshot"]["regimen"]["schedule"] == "утром и вечером"
    # Повторный подбор не даёт второго назначения того же препарата.
    [blocked] = again["options"]
    assert {c["id"]: c["status"] for c in blocked["checks"]}["not_prescribed"] == "fail"
    assert blocked["proposal"] is None
    assert [(s["trade_name"], s["dose_mg"], s["times_per_day"]) for s in schedule] == [
        ("Тестин", 10, 2)
    ]


async def test_revising_within_label_replaces_the_system_proposal(care: Care) -> None:
    option = await ready(care)
    proposal_id = option["proposal"]["id"]

    response = await care.client.post(
        f"/api/v1/therapy/proposals/{proposal_id}/revise",
        json={"regimen": {"dose_mg": 10, "times_per_day": 2, "duration_days": 60}},
    )
    proposals = (await care.client.get(f"{care.base}/proposals")).json()

    assert response.status_code == 200, response.text
    assert response.json()["duration_days"] == 60
    assert response.json()["off_label"] is False
    by_origin = {p["origin_type"]: p for p in proposals}
    assert by_origin["rule"]["status"] == "rejected"
    assert by_origin["rule"]["decision_comment"] == "изменено врачом"
    assert by_origin["doctor"]["status"] == "confirmed"


async def test_changed_frequency_does_not_keep_the_label_schedule(care: Care) -> None:
    option = await ready(care)
    url = f"/api/v1/therapy/proposals/{option['proposal']['id']}/revise"
    reason = "Переносимость: сонливость при двукратном приёме"

    response = await care.client.post(
        url,
        json={
            "regimen": {"dose_mg": 10, "times_per_day": 1, "duration_days": None},
            "comment": reason,
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["schedule"] == "время приёма определяет врач"


async def test_changed_frequency_takes_the_doctors_schedule(care: Care) -> None:
    option = await ready(care)
    url = f"/api/v1/therapy/proposals/{option['proposal']['id']}/revise"
    regimen = {"dose_mg": 10, "times_per_day": 1, "duration_days": None, "schedule": "вечером"}

    response = await care.client.post(
        url, json={"regimen": regimen, "comment": "Переносимость: сонливость днём"}
    )

    assert response.json()["schedule"] == "вечером"


async def test_off_label_needs_a_reason(care: Care) -> None:
    option = await ready(care)
    url = f"/api/v1/therapy/proposals/{option['proposal']['id']}/revise"
    regimen = {"dose_mg": 20, "times_per_day": 2, "duration_days": None}

    without = await care.client.post(url, json={"regimen": regimen})
    short = await care.client.post(url, json={"regimen": regimen, "comment": "надо"})
    reasoned = await care.client.post(
        url, json={"regimen": regimen, "comment": "Решение врачебной комиссии от 18.10"}
    )

    assert (without.status_code, short.status_code) == (422, 422)
    assert reasoned.status_code == 200
    assert (reasoned.json()["off_label"], reasoned.json()["off_label_reason"]) == (
        True,
        "Решение врачебной комиссии от 18.10",
    )


async def test_profile_change_rejects_open_proposals(care: Care) -> None:
    option = await ready(care)

    await set_profile(care, birth_date="1990-01-01")
    proposals = (await care.client.get(f"{care.base}/proposals")).json()

    [proposal] = [p for p in proposals if p["id"] == option["proposal"]["id"]]
    assert (proposal["status"], proposal["decision_comment"]) == ("rejected", "профиль изменился")


async def test_diagnosis_change_rejects_open_proposals(care: Care) -> None:
    option = await ready(care)

    await add_diagnosis(care, {"icd10": "F32.1", "title": "Депрессивный эпизод"})
    proposals = (await care.client.get(f"{care.base}/proposals")).json()

    assert proposals[0]["id"] == option["proposal"]["id"]
    assert proposals[0]["status"] == "rejected"


async def test_stop_is_a_confirmed_proposal_with_reason(care: Care) -> None:
    option = await ready(care)
    await care.client.post(f"/api/v1/proposals/{option['proposal']['id']}/confirm", json={})
    [prescription] = await prescriptions(care)

    stopped = await care.client.post(
        f"/api/v1/prescriptions/{prescription['id']}/stop",
        json={"reason": "Нежелательная реакция: скованность мышц"},
    )
    [after] = await prescriptions(care)
    proposals = (await care.client.get(f"{care.base}/proposals")).json()
    care.client.cookies.clear()
    await login(care.client, PATIENT)
    schedule = (await care.client.get("/api/v1/me/prescriptions")).json()

    assert stopped.status_code == 200
    assert (after["status"], after["stop_reason"]) == (
        "stopped",
        "Нежелательная реакция: скованность мышц",
    )
    assert after["stopped_by"] is not None and after["stopped_at"] is not None
    stop = next(p for p in proposals if p["kind"] == "therapy.stop")
    assert (stop["status"], stop["origin_type"]) == ("confirmed", "doctor")
    assert schedule == []


async def test_course_ends_by_itself(
    care: Care, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    await set_profile(care)
    await add_diagnosis(care, {"icd10": "F43.2", "title": "Расстройство адаптации"})
    await add_diagnosis(care, {"icd10": "U09.9", "title": "Состояние после COVID-19"})
    [option] = (await options(care))["options"]
    assert {c["id"]: c["status"] for c in option["checks"]}["manual-0"] == "manual"
    await care.client.post(f"/api/v1/proposals/{option['proposal']['id']}/confirm", json={})
    async with sessionmaker() as session:
        await session.execute(
            text("UPDATE therapy.prescriptions SET started_at = started_at - interval '29 days'")
        )
        await session.commit()

    [prescription] = await prescriptions(care)
    care.client.cookies.clear()
    await login(care.client, PATIENT)
    schedule = (await care.client.get("/api/v1/me/prescriptions")).json()

    assert (prescription["duration_days"], prescription["status"]) == (28, "completed")
    assert schedule == []


async def test_other_doctor_and_patient_cannot_touch_therapy(
    care: Care, make_user: MakeUser, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    option = await ready(care)
    await care.client.post(f"/api/v1/proposals/{option['proposal']['id']}/confirm", json={})
    [prescription] = await prescriptions(care)
    await make_user(email="other@example.com")
    care.client.cookies.clear()
    await login(care.client, "other@example.com")

    responses = [
        await care.client.get(f"{care.base}/diagnoses"),
        await care.client.put(f"{care.base}/profile", json={"birth_date": None, "sex": None}),
        await care.client.post(f"{care.base}/therapy/options"),
        await care.client.get(f"{care.base}/prescriptions"),
        await care.client.post(f"{care.base}/diagnoses", json=GAD),
        await care.client.post(
            f"/api/v1/therapy/proposals/{option['proposal']['id']}/revise",
            json={"regimen": {"dose_mg": 10, "times_per_day": 2, "duration_days": None}},
        ),
        await care.client.post(
            f"/api/v1/prescriptions/{prescription['id']}/stop", json={"reason": "чужой врач"}
        ),
    ]
    care.client.cookies.clear()
    await login(care.client, PATIENT)
    patient = await care.client.post(f"{care.base}/therapy/options")

    assert [r.status_code for r in responses] == [404] * 7
    assert patient.status_code == 403
    async with sessionmaker() as session:
        denied = (
            await session.scalars(
                text("SELECT action FROM identity.access_log WHERE action LIKE '%.denied'")
            )
        ).all()
    assert sorted(denied) == sorted(
        [
            "patient.diagnoses.list.denied",
            "patient.profile.update.denied",
            "patient.therapy.options.denied",
            "patient.prescriptions.list.denied",
            "patient.diagnoses.add.denied",
            "therapy.revise.denied",
            "therapy.stop.denied",
        ]
    )


async def test_reads_and_changes_are_written_to_access_log(
    care: Care, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    await ready(care)

    async with sessionmaker() as session:
        actions = set((await session.scalars(text("SELECT action FROM identity.access_log"))).all())
    assert {
        "patient.profile.update",
        "patient.diagnoses.add",
        "patient.therapy.options",
    } <= actions
