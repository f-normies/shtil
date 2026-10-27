"""API сводки на настоящей БД (спека #38, 3.1, 3.4, 3.5): период, ключ, подтверждение, доступ.

Черновик пишет задача worker'а на `FunctionModel` (`helpers.drafted`).
"""

from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.modules.alerts import service as alerts
from clin_compass.modules.patients import service as patients
from tests.conftest import MakeUser
from tests.patients.test_access import login, register_patient
from tests.summary.helpers import (
    SLEEP,
    STATEMENT,
    SummaryCare,
    add_finding,
    add_result,
    confirm,
    drafted,
    finish_jobs,
    get_summary,
    jobs,
    rows,
)

EDITED = {"text": "Пациент жалуется на плохой сон.", "facts": [1]}


async def _registered(maker: async_sessionmaker[AsyncSession], care: SummaryCare) -> datetime:
    async with maker() as session:
        moment = await patients.registered_at(session, care.patient_id)
    assert moment is not None
    return moment


def _at(value: str) -> datetime:
    return datetime.fromisoformat(value)


async def test_without_facts_summary_is_empty_and_nothing_is_queued(
    care: SummaryCare, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    shown = await get_summary(care)

    assert shown["status"] == "empty"
    assert (shown["id"], shown["period_end"], shown["stale"]) == (None, None, False)
    assert (shown["facts"], shown["statements"]) == ([], [])
    assert _at(shown["period_start"]) == await _registered(sessionmaker, care)
    assert await rows(sessionmaker) == []
    assert await jobs(sessionmaker) == []


async def test_first_summary_starts_at_registration_and_queues_a_draft(
    care: SummaryCare, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    await add_finding(sessionmaker, care)

    shown = await get_summary(care)

    assert (shown["status"], shown["stale"], shown["statements"]) == ("pending", False, [])
    assert _at(shown["period_start"]) == await _registered(sessionmaker, care)
    [fact] = shown["facts"]
    assert (fact["number"], fact["kind"], fact["section"], fact["text"]) == (
        1,
        "finding",
        "diary",
        SLEEP,
    )
    [row] = await rows(sessionmaker)
    assert (str(row["id"]), row["status"], row["failures"]) == (shown["id"], "pending", 0)
    assert len(row["data_key"]) == 64
    assert await jobs(sessionmaker) == [(row["data_key"], f"summary.draft:{row['data_key']}")]


async def test_repeated_get_with_same_data_keeps_one_row(
    care: SummaryCare, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    await add_finding(sessionmaker, care)

    first = await get_summary(care)
    second = await get_summary(care)

    assert first["id"] == second["id"]
    assert len(await rows(sessionmaker)) == 1
    assert len(await jobs(sessionmaker)) == 1


async def test_scales_signals_and_findings_become_facts(
    care: SummaryCare, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    await add_result(sessionmaker, care, 16, "Умеренная депрессия")
    async with sessionmaker() as session:
        await alerts.raise_signal(
            session,
            patient_id=care.patient_id,
            title="Мысли о смерти в дневнике",
            doctor_actions=["Связаться с пациентом"],
            evidence={},
            ai_run_id=uuid4(),
        )
        await session.commit()
    await add_finding(sessionmaker, care)

    shown = await get_summary(care)

    assert [(f["kind"], f["section"]) for f in shown["facts"]] == [
        ("scale", "assessments"),
        ("signal", "signals"),
        ("finding", "diary"),
    ]
    assert shown["facts"][0]["text"].endswith(
        ": 16 баллов, умеренная депрессия. Первое заполнение шкалы."
    )
    assert shown["facts"][1]["text"] == (
        "Сигнал «обязательно»: Мысли о смерти в дневнике. Состояние: новый."
    )


async def test_confirmed_summary_keeps_system_draft_next_to_doctor_version(
    care: SummaryCare, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    await add_finding(sessionmaker, care)
    draft = await drafted(sessionmaker, care)

    response = await confirm(care, draft["id"], [EDITED])

    assert response.status_code == 200, response.text
    shown = response.json()
    assert (shown["status"], shown["edited"], shown["statements"]) == ("confirmed", True, [EDITED])
    assert shown["confirmed_by"] == str(care.doctor.id)
    assert shown["confirmed_by_name"] == care.doctor.full_name
    assert shown["confirmed_at"] is not None
    [row] = await rows(sessionmaker)
    assert (row["draft"], row["confirmed_statements"]) == ([STATEMENT], [EDITED])


async def test_unchanged_confirmation_is_not_marked_edited(
    care: SummaryCare, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    await add_finding(sessionmaker, care)
    draft = await drafted(sessionmaker, care)

    shown = (await confirm(care, draft["id"], draft["statements"])).json()

    assert (shown["status"], shown["edited"]) == ("confirmed", False)


async def test_after_confirmation_new_period_starts_at_confirmation(
    care: SummaryCare, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    await add_finding(sessionmaker, care)
    draft = await drafted(sessionmaker, care)
    confirmed = (await confirm(care, draft["id"], [EDITED])).json()

    unchanged = await get_summary(care)
    await add_finding(sessionmaker, care, summary="тревога по утрам", quote="Тревожно утром.")
    renewed = await get_summary(care)

    assert (unchanged["id"], unchanged["status"]) == (draft["id"], "confirmed")
    assert unchanged["statements"] == [EDITED]
    assert renewed["id"] != draft["id"]
    assert (renewed["status"], renewed["stale"]) == ("pending", False)
    assert renewed["period_start"] == confirmed["confirmed_at"]
    assert [f["text"] for f in renewed["facts"]] == [
        "Дневник, симптом: тревога по утрам. Цитата: «Тревожно утром.»"
    ]


async def test_new_data_before_confirmation_shows_previous_draft_as_stale(
    care: SummaryCare, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    await add_finding(sessionmaker, care)
    draft = await drafted(sessionmaker, care)

    await add_finding(sessionmaker, care, summary="тревога по утрам", quote="Тревожно утром.")
    shown = await get_summary(care)

    assert (shown["id"], shown["status"], shown["stale"]) == (draft["id"], "pending", True)
    assert shown["statements"] == [STATEMENT]
    assert shown["facts"] == draft["facts"]
    old, new = await rows(sessionmaker)
    assert (old["status"], new["status"]) == ("done", "pending")
    assert old["data_key"] != new["data_key"]
    assert await jobs(sessionmaker) == [(new["data_key"], f"summary.draft:{new['data_key']}")]


async def test_stale_draft_cannot_be_confirmed(
    care: SummaryCare, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    await add_finding(sessionmaker, care)
    draft = await drafted(sessionmaker, care)
    await add_finding(sessionmaker, care, summary="тревога по утрам", quote="Тревожно утром.")

    response = await confirm(care, draft["id"], draft["statements"])

    assert response.status_code == 409
    assert response.json()["code"] == "stale"
    assert response.json()["detail"] == "Данные изменились, обновите сводку"


async def test_second_confirmation_is_a_conflict(
    care: SummaryCare, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    await add_finding(sessionmaker, care)
    draft = await drafted(sessionmaker, care)
    await confirm(care, draft["id"], draft["statements"])

    response = await confirm(care, draft["id"], [EDITED])

    assert response.status_code == 409
    assert response.json()["code"] == "already_confirmed"
    [row] = await rows(sessionmaker)
    assert row["confirmed_statements"] == [STATEMENT]


async def test_pending_summary_cannot_be_confirmed(
    care: SummaryCare, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    await add_finding(sessionmaker, care)
    pending = await get_summary(care)

    response = await confirm(care, pending["id"], [STATEMENT])

    assert response.status_code == 409
    assert response.json()["code"] == "stale"


async def test_invalid_doctor_version_is_rejected(
    care: SummaryCare, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    await add_finding(sessionmaker, care)
    draft = await drafted(sessionmaker, care)
    bad: list[list[dict[str, Any]]] = [
        [],
        [{"text": "   ", "facts": [1]}],
        [{"text": "Без фактов.", "facts": []}],
        [{"text": "а" * 501, "facts": [1]}],
        [STATEMENT] * 6,
    ]

    statuses = [(await confirm(care, draft["id"], body)).status_code for body in bad]
    unknown = await confirm(care, draft["id"], [{"text": "Чужой факт.", "facts": [1, 2]}])

    assert statuses == [422] * len(bad)
    assert unknown.status_code == 422
    assert unknown.json()["code"] == "unknown_fact"
    [row] = await rows(sessionmaker)
    assert row["status"] == "done"


async def test_doctor_cannot_add_statements_or_facts_beyond_the_draft(
    care: SummaryCare, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    await add_finding(sessionmaker, care)
    await add_finding(sessionmaker, care, summary="тревога по утрам", quote="Тревожно утром.")
    # Черновик — одно утверждение по факту 1; факт 2 в снимке есть, черновик на него не ссылается.
    draft = await drafted(sessionmaker, care)
    assert [f["number"] for f in draft["facts"]] == [1, 2]

    more = await confirm(care, draft["id"], [STATEMENT, EDITED])
    other_fact = await confirm(care, draft["id"], [{"text": "Тревога по утрам.", "facts": [2]}])
    allowed = await confirm(care, draft["id"], [EDITED])

    assert (more.status_code, more.json()["code"]) == (422, "too_many_statements")
    assert (other_fact.status_code, other_fact.json()["code"]) == (422, "unknown_fact")
    assert allowed.status_code == 200


async def test_unknown_summary_is_not_found(
    care: SummaryCare, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    await add_finding(sessionmaker, care)
    await get_summary(care)

    response = await confirm(care, str(uuid4()), [STATEMENT])

    assert response.status_code == 404


async def test_other_doctors_patient_is_not_found_and_logged(
    care: SummaryCare, sessionmaker: async_sessionmaker[AsyncSession], make_user: MakeUser
) -> None:
    await add_finding(sessionmaker, care)
    draft = await drafted(sessionmaker, care)
    await make_user(email="other@example.com")
    await login(care.client, "other@example.com")

    shown = await care.client.get(care.base)
    confirmed = await confirm(care, draft["id"], draft["statements"])
    retried = await care.client.post(f"{care.base}/retry")

    assert [shown.status_code, confirmed.status_code, retried.status_code] == [404, 404, 404]
    async with sessionmaker() as session:
        actions = (
            await session.scalars(
                text(
                    "SELECT action FROM identity.access_log"
                    " WHERE action LIKE 'patient.summary%' ORDER BY id"
                )
            )
        ).all()
    assert actions[-3:] == [
        "patient.summary.denied",
        "patient.summary.confirm.denied",
        "patient.summary.retry.denied",
    ]
    [row] = await rows(sessionmaker)
    assert row["status"] == "done"


async def test_summary_of_another_patient_of_same_doctor_is_not_found(
    care: SummaryCare, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    await add_finding(sessionmaker, care)
    draft = await drafted(sessionmaker, care)
    second = await register_patient(care.client, "doctor@example.com", "second@example.com")
    await login(care.client, "doctor@example.com")
    other = SummaryCare(care.client, care.doctor, second)
    await finish_jobs(sessionmaker)

    response = await confirm(other, draft["id"], draft["statements"])

    assert response.status_code == 404
