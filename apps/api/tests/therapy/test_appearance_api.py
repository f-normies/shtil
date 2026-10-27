"""Вид таблетки в пациентском расписании и его персональная CAS-поправка."""

import asyncio
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.patients.test_access import login, register_patient
from tests.therapy.conftest import DOCTOR, PATIENT, Care
from tests.therapy.test_api import prescriptions, ready

OTHER_PATIENT = "other@example.com"


async def prescribe(care: Care) -> dict[str, Any]:
    option = await ready(care)
    response = await care.client.post(
        f"/api/v1/proposals/{option['proposal']['id']}/confirm", json={}
    )
    assert response.status_code == 200, response.text
    [prescription] = await prescriptions(care)
    return prescription


async def as_patient(care: Care, email: str = PATIENT) -> None:
    care.client.cookies.clear()
    await login(care.client, email)


async def test_patient_schedule_shows_sourced_default_for_unique_form_title(care: Care) -> None:
    await prescribe(care)
    await as_patient(care)

    response = await care.client.get("/api/v1/me/prescriptions")

    assert response.status_code == 200, response.text
    [item] = response.json()
    assert item["default_appearance"] == {
        "shape": "round",
        "color": "white",
        "description": "Круглая белая таблетка по тестовой инструкции.",
        "source": {
            "source": "test-label",
            "locator": "3",
            "title": "Тестовая инструкция",
            "url": "https://example.org/label",
            "version": "1",
            "date": "2025-01-01",
        },
    }
    assert item["appearance"] == {"shape": "round", "color": "white"}
    assert (item["appearance_origin"], item["appearance_revision"]) == ("instruction", 0)
    assert item["trade_name"] == "Тестин"


async def test_override_revision_and_null_reset_are_reflected_in_schedule(care: Care) -> None:
    prescription = await prescribe(care)
    await as_patient(care)
    url = f"/api/v1/me/prescriptions/{prescription['id']}/appearance"

    changed = await care.client.put(
        url,
        json={"expected_revision": 0, "appearance": {"shape": "oval", "color": "peach"}},
    )
    stale = await care.client.put(
        url,
        json={"expected_revision": 0, "appearance": {"shape": "capsule", "color": "blue"}},
    )
    schedule = await care.client.get("/api/v1/me/prescriptions")
    reset = await care.client.put(url, json={"expected_revision": 1, "appearance": None})
    after_reset = await care.client.get("/api/v1/me/prescriptions")

    assert changed.status_code == 200, changed.text
    assert changed.json()["appearance"] == {"shape": "oval", "color": "peach"}
    assert (changed.json()["appearance_origin"], changed.json()["appearance_revision"]) == (
        "patient",
        1,
    )
    assert stale.status_code == 409
    [item] = schedule.json()
    assert item["appearance"] == {"shape": "oval", "color": "peach"}
    assert item["appearance_origin"] == "patient"
    assert reset.status_code == 200, reset.text
    assert reset.json()["appearance"] == {"shape": "round", "color": "white"}
    assert (reset.json()["appearance_origin"], reset.json()["appearance_revision"]) == (
        "instruction",
        2,
    )
    [item] = after_reset.json()
    assert item["appearance"] == {"shape": "round", "color": "white"}
    assert item["appearance_revision"] == 2


async def test_parallel_first_updates_have_one_compare_and_swap_winner(care: Care) -> None:
    prescription = await prescribe(care)
    await as_patient(care)
    url = f"/api/v1/me/prescriptions/{prescription['id']}/appearance"
    body = {"expected_revision": 0, "appearance": {"shape": "oval", "color": "sand"}}

    first, second = await asyncio.gather(
        care.client.put(url, json=body),
        care.client.put(url, json=body),
    )

    assert sorted([first.status_code, second.status_code]) == [200, 409]
    [item] = (await care.client.get("/api/v1/me/prescriptions")).json()
    assert (item["appearance_origin"], item["appearance_revision"]) == ("patient", 1)


async def test_nonmatching_snapshot_form_returns_unknown_appearance(
    care: Care,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    prescription = await prescribe(care)
    async with sessionmaker() as session:
        result = await session.execute(
            text(
                "UPDATE therapy.prescriptions "
                "SET card_snapshot = jsonb_set(card_snapshot, '{regimen,form}', "
                "to_jsonb(CAST(:form AS text))) WHERE id = :id"
            ),
            {"form": "форма из старого снимка", "id": UUID(prescription["id"])},
        )
        await session.commit()
    assert result.rowcount == 1
    await as_patient(care)

    response = await care.client.get("/api/v1/me/prescriptions")

    assert response.status_code == 200, response.text
    [item] = response.json()
    assert item["default_appearance"] is None
    assert item["appearance"] is None
    assert (item["appearance_origin"], item["appearance_revision"]) == ("unknown", 0)


async def test_appearance_update_hides_another_patients_prescription(care: Care) -> None:
    other_patient_id = await register_patient(care.client, DOCTOR, OTHER_PATIENT)
    await login(care.client, DOCTOR)
    other = Care(care.client, other_patient_id)
    prescription = await prescribe(other)
    await as_patient(care)

    response = await care.client.put(
        f"/api/v1/me/prescriptions/{prescription['id']}/appearance",
        json={"expected_revision": 0, "appearance": {"shape": "oval", "color": "peach"}},
    )

    assert response.status_code == 404


async def test_appearance_update_rejects_own_inactive_prescription(care: Care) -> None:
    prescription = await prescribe(care)
    stopped = await care.client.post(
        f"/api/v1/prescriptions/{prescription['id']}/stop",
        json={"reason": "Нежелательная реакция"},
    )
    assert stopped.status_code == 200, stopped.text
    await as_patient(care)

    response = await care.client.put(
        f"/api/v1/me/prescriptions/{prescription['id']}/appearance",
        json={"expected_revision": 0, "appearance": {"shape": "oval", "color": "peach"}},
    )

    assert response.status_code == 409


async def test_appearance_update_rejects_values_outside_the_supported_set(care: Care) -> None:
    prescription = await prescribe(care)
    await as_patient(care)

    response = await care.client.put(
        f"/api/v1/me/prescriptions/{prescription['id']}/appearance",
        json={"expected_revision": 0, "appearance": {"shape": "triangle", "color": "white"}},
    )

    assert response.status_code == 422
