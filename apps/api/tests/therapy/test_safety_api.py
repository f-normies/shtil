"""Безопасность терапии на настоящей БД (спека #29, раздел 8).

Справочник — `tests/therapy/knowledge`: у `test-drug` противопоказания «ингибиторы МАО
и 2 недели после отмены» (моклобемид) и «СКФ < 60», мера предосторожности —
серотонинергические препараты (суматриптан). В снимке реестра моклобемид снят с регистрации.
"""

from datetime import date, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.conftest import MakeUser
from tests.patients.test_access import login
from tests.therapy.conftest import Care
from tests.therapy.test_api import GAD, add_diagnosis, options, prescriptions

PROFILE = {"birth_date": "1992-05-01", "sex": "female", "egfr": 95, "child_pugh": "none"}


def days_ago(n: int) -> str:
    return (date.today() - timedelta(days=n)).isoformat()


async def set_facts(care: Care, **changes: Any) -> dict[str, Any]:
    response = await care.client.put(f"{care.base}/profile", json={**PROFILE, **changes})
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


async def add_medication(care: Care, name: str, **fields: Any) -> dict[str, Any]:
    response = await care.client.post(f"{care.base}/medications", json={"name": name, **fields})
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


async def safety_alerts(care: Care) -> list[dict[str, Any]]:
    alerts = (await care.client.get(f"{care.base}/alerts")).json()
    return [a for a in alerts if a["kind"] == "safety"]


async def prescribed(care: Care) -> dict[str, Any]:
    """Профиль, F41.1 и подтверждённое назначение `test-drug`."""
    await set_facts(care)
    await add_diagnosis(care, GAD)
    [option] = (await options(care))["options"]
    confirmed = await care.client.post(
        f"/api/v1/proposals/{option['proposal']['id']}/confirm", json={}
    )
    assert confirmed.status_code == 200, confirmed.text
    [prescription] = await prescriptions(care)
    return prescription


async def test_profile_facts_are_saved(care: Care) -> None:
    summary = await set_facts(care, egfr=58, egfr_measured_on="2026-10-01", child_pugh="A")

    assert (summary["egfr"], summary["egfr_measured_on"], summary["child_pugh"]) == (
        58,
        "2026-10-01",
        "A",
    )


async def test_profile_update_keeps_facts_it_does_not_send(care: Care) -> None:
    # Ревью #76: запрос только с датой рождения и полом (так пишет сид) стирал СКФ и класс.
    await set_facts(care, egfr=58, child_pugh="A")

    response = await care.client.put(
        f"{care.base}/profile", json={"birth_date": "1992-05-01", "sex": "female"}
    )
    saved = (await care.client.get(care.base)).json()

    assert response.status_code == 200, response.text
    assert (saved["egfr"], saved["child_pugh"]) == (58, "A")


async def test_profile_update_clears_a_fact_sent_as_null(care: Care) -> None:
    await set_facts(care, egfr=58)

    await set_facts(care, egfr=None)
    saved = (await care.client.get(care.base)).json()

    assert saved["egfr"] is None


async def test_medication_gets_inn_even_if_registration_is_inactive(care: Care) -> None:
    medication = await add_medication(care, "Аурорикс", stopped_on=days_ago(10))

    assert (medication["inn"], medication["registry_check"]) == ("Моклобемид", "inactive")


async def test_trade_name_with_several_registrations_blocks_like_its_inn(care: Care) -> None:
    # Ревью #76: «Пиразидол» с тремя удостоверениями не давал МНН, и препарат предлагался.
    await set_facts(care)
    await add_diagnosis(care, GAD)
    medication = await add_medication(care, "Маоин", stopped_on=days_ago(10))

    [option] = (await options(care))["options"]

    maoi = next(c for c in option["checks"] if c["id"] == "contra:maoi")
    assert medication["inn"] == "Моклобемид"
    assert (maoi["status"], option["proposal"]) == ("fail", None)


async def test_registry_entry_without_inn_asks_the_doctor(care: Care) -> None:
    await set_facts(care)
    await add_diagnosis(care, GAD)
    medication = await add_medication(care, "Безмннин")

    [option] = (await options(care))["options"]

    assert medication["inn"] is None
    assert [c["status"] for c in option["checks"] if c["id"].startswith("unmatched:")] == ["manual"]


async def test_drug_lookup_offers_similar_names(care: Care) -> None:
    response = await care.client.get("/api/v1/therapy/drug-lookup", params={"name": "Аурорик"})

    body = response.json()
    assert body["check"] == "similar"
    assert {m["inn"] for m in body["matches"]} == {"Моклобемид"}


async def test_maoi_stopped_10_days_ago_gives_no_proposal(care: Care) -> None:
    # Сценарий 3, шаг s5.
    await set_facts(care)
    await add_diagnosis(care, GAD)
    await add_medication(care, "Аурорикс", stopped_on=days_ago(10))

    [option] = (await options(care))["options"]

    maoi = next(c for c in option["checks"] if c["id"] == "contra:maoi")
    # 14-й день после отмены ещё в сроке: можно назначать с 15-го, через 5 дней.
    first_day = (date.today() + timedelta(days=5)).strftime("%d.%m.%Y")
    assert (maoi["status"], option["proposal"]) == ("fail", None)
    assert f"можно назначать с {first_day}" in maoi["detail"]


async def test_new_maoi_on_active_prescription_raises_one_safety_signal(care: Care) -> None:
    await prescribed(care)

    await add_medication(care, "Аурорикс")
    await set_facts(care, egfr=96)
    [signal] = await safety_alerts(care)

    assert (signal["level"], signal["rule_id"]) == ("mandatory", "test-drug/maoi")
    assert signal["occurrences"] == 1
    # Все источники правила, а не только первый (ревью #76).
    assert [s["locator"] for s in signal["sources"]] == ["4.3", "4.5"]
    # Вид сигнала: по нему #95 показывает врачу варианты решения.
    assert signal["triggers"][0]["evidence"]["safety_kind"] == "contraindication"


async def test_warning_known_at_prescribing_does_not_signal_again(care: Care) -> None:
    # Сценарий 2: суматриптан известен с назначения, правка СКФ не повторяет предупреждение.
    await set_facts(care)
    await add_diagnosis(care, GAD)
    await add_medication(care, "Суматриптан")
    [option] = (await options(care))["options"]
    assert (
        next(c for c in option["checks"] if c["id"] == "caution:serotonergic")["status"] == "warn"
    )
    await care.client.post(f"/api/v1/proposals/{option['proposal']['id']}/confirm", json={})

    await set_facts(care, egfr=90)

    assert await safety_alerts(care) == []


async def test_new_medication_without_inn_on_active_prescription_signals(care: Care) -> None:
    # Взаимодействия препарата без МНН не проверены: врач должен узнать о нём.
    await prescribed(care)

    await add_medication(care, "Неизвестин")
    await set_facts(care, egfr=96)
    [signal] = await safety_alerts(care)

    assert (signal["level"], signal["rule_id"]) == ("desirable", "test-drug/unmatched:неизвестин")
    assert "Неизвестин" in signal["title"]
    assert signal["occurrences"] == 1
    assert signal["triggers"][0]["evidence"]["safety_kind"] == "caution"


async def test_each_new_medication_without_inn_gets_its_own_signal(care: Care) -> None:
    # Ревью #76: второй препарат без МНН дописывался к сигналу первого и терялся.
    await prescribed(care)

    await add_medication(care, "Неизвестин")
    await add_medication(care, "Другин")
    signals = await safety_alerts(care)

    assert sorted((s["rule_id"], s["title"].count("Другин")) for s in signals) == [
        ("test-drug/unmatched:другин", 1),
        ("test-drug/unmatched:неизвестин", 0),
    ]


async def test_rule_that_worsens_again_appends_to_the_open_signal(care: Care) -> None:
    await prescribed(care)

    await set_facts(care, egfr=45)
    await set_facts(care, egfr=95)
    await set_facts(care, egfr=45)
    [signal] = await safety_alerts(care)

    assert (signal["rule_id"], signal["occurrences"]) == ("test-drug/renal", 2)


async def test_worse_fact_raises_mandatory_signal(care: Care) -> None:
    await prescribed(care)

    await set_facts(care, egfr=45)
    [signal] = await safety_alerts(care)

    assert (signal["level"], signal["rule_id"]) == ("mandatory", "test-drug/renal")


async def test_adding_a_medication_rejects_open_proposals(care: Care) -> None:
    await set_facts(care)
    await add_diagnosis(care, GAD)
    [option] = (await options(care))["options"]

    await add_medication(care, "Аурорикс")
    proposals = (await care.client.get(f"{care.base}/proposals")).json()

    [proposal] = [p for p in proposals if p["id"] == option["proposal"]["id"]]
    assert proposal["status"] == "rejected"


async def test_stopping_a_medication_keeps_the_row(care: Care) -> None:
    medication = await add_medication(care, "Суматриптан")

    stopped = await care.client.post(
        f"{care.base}/medications/{medication['id']}/stop", json={"stopped_on": days_ago(1)}
    )
    listed = (await care.client.get(f"{care.base}/medications")).json()

    assert stopped.status_code == 200
    assert [(m["name"], m["stopped_on"]) for m in listed] == [("Суматриптан", days_ago(1))]


async def test_medication_cannot_stop_before_it_started(care: Care) -> None:
    response = await care.client.post(
        f"{care.base}/medications",
        json={"name": "Суматриптан", "started_on": days_ago(1), "stopped_on": days_ago(3)},
    )

    assert response.status_code == 422


async def test_stop_date_cannot_be_before_the_start(care: Care) -> None:
    medication = await add_medication(care, "Суматриптан", started_on=days_ago(2))

    stopped = await care.client.post(
        f"{care.base}/medications/{medication['id']}/stop", json={"stopped_on": days_ago(5)}
    )
    listed = (await care.client.get(f"{care.base}/medications")).json()

    assert stopped.status_code == 422
    assert [m["stopped_on"] for m in listed] == [None]


async def test_other_doctor_cannot_touch_medications(
    care: Care, make_user: MakeUser, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    medication = await add_medication(care, "Суматриптан")
    await make_user(email="other@example.com")
    care.client.cookies.clear()
    await login(care.client, "other@example.com")

    responses = [
        await care.client.get(f"{care.base}/medications"),
        await care.client.post(f"{care.base}/medications", json={"name": "Аурорикс"}),
        await care.client.post(
            f"{care.base}/medications/{medication['id']}/stop", json={"stopped_on": days_ago(0)}
        ),
    ]

    assert [r.status_code for r in responses] == [404, 404, 404]
    async with sessionmaker() as session:
        denied = (
            await session.scalars(
                text(
                    "SELECT action FROM identity.access_log "
                    "WHERE action LIKE 'patient.medications%.denied'"
                )
            )
        ).all()
    assert len(denied) == 3
