"""Подбор и подтверждение проверяют текущие данные пациента (спека #28, раздел 5).

Открытое предложение переживает смену статуса карточки, обновление снимка реестра и правку
профиля в обход ручек. Подтверждение заново сверяет карточку и проверки. Подбор, который ждал
блокировку, пока менялся профиль, проверяет уже новый профиль.
"""

import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.therapy.conftest import Care
from tests.therapy.test_api import add_diagnosis, options, prescriptions, ready, set_profile


async def confirm(care: Care, proposal_id: str) -> tuple[int, str]:
    response = await care.client.post(f"/api/v1/proposals/{proposal_id}/confirm", json={})
    detail = response.json().get("detail", "") if response.status_code != 200 else ""
    return response.status_code, detail


async def status_of(care: Care, proposal_id: str) -> str:
    proposals = (await care.client.get(f"{care.base}/proposals")).json()
    return str(next(p["status"] for p in proposals if p["id"] == proposal_id))


async def run_sql(sessionmaker: async_sessionmaker[AsyncSession], sql: str) -> None:
    async with sessionmaker() as session:
        await session.execute(text(sql))
        await session.commit()


async def test_confirm_applies_a_proposal_whose_checks_still_pass(care: Care) -> None:
    option = await ready(care)

    code, _ = await confirm(care, option["proposal"]["id"])

    assert code == 200
    assert [p["drug_id"] for p in await prescriptions(care)] == ["test-drug"]


async def test_confirm_refuses_a_proposal_built_from_another_card(
    care: Care, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    # Хеш в предложении не совпадает с карточкой справочника: инструкцию обновили после подбора.
    option = await ready(care)
    await run_sql(
        sessionmaker,
        "UPDATE proposals.proposals SET payload = jsonb_set(payload, '{card_hash}', '\"old\"')",
    )

    code, detail = await confirm(care, option["proposal"]["id"])

    assert (code, detail) == (
        422,
        "Карточка препарата в справочнике изменилась после подбора. Подберите терапию заново",
    )
    assert await prescriptions(care) == []
    assert await status_of(care, option["proposal"]["id"]) == "proposed"


@pytest.mark.parametrize(
    ("sql", "failed"),
    [
        ("UPDATE patients.patients SET birth_date = '2012-01-01'", "Возраст от 18 лет — "),
        ("UPDATE patients.diagnoses SET resolved_at = now()", "— нет диагноза F41.1"),
    ],
)
async def test_confirm_refuses_a_proposal_that_fails_checks_now(
    care: Care, sessionmaker: async_sessionmaker[AsyncSession], sql: str, failed: str
) -> None:
    # Профиль изменён в обход ручек: открытое предложение не отклонено, проверки устарели.
    option = await ready(care)
    await run_sql(sessionmaker, sql)

    code, detail = await confirm(care, option["proposal"]["id"])

    assert code == 422
    assert detail.startswith("Проверка по текущим данным пациента не пройдена: ")
    assert failed in detail
    assert detail.endswith(". Подберите терапию заново")
    assert await prescriptions(care) == []


async def waiting_for_lock(sessionmaker: async_sessionmaker[AsyncSession]) -> None:
    """Ждёт, пока чей-то запрос встанет в очередь за рекомендательной блокировкой."""
    sql = text("SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND NOT granted")
    async with asyncio.timeout(10):
        while True:
            async with sessionmaker() as session:
                if await session.scalar(sql):
                    return
            await asyncio.sleep(0.02)


async def test_options_check_the_profile_saved_while_waiting_for_the_lock(
    care: Care, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    # Подбор прочитал пациента до блокировки. Профиль сменили, пока подбор ждал её:
    # предложение по старой дате рождения отклонение открытых предложений уже не застало бы.
    await set_profile(care)
    await add_diagnosis(care)
    key = f"therapy.options:{care.patient_id}"
    async with sessionmaker() as holder:
        await holder.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": key}
        )
        pending = asyncio.create_task(options(care))
        await waiting_for_lock(sessionmaker)
        await holder.execute(text("UPDATE patients.patients SET birth_date = '2012-01-01'"))
        await holder.commit()
    [option] = (await pending)["options"]

    assert {c["id"]: c["status"] for c in option["checks"]}["age"] == "fail"
    assert option["proposal"] is None
