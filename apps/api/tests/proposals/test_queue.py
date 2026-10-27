"""Очередь предложений по всем пациентам врача и прогресс разбора (спека #96, разделы 3.1–3.4)."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.core.ai.runs import AiRun, DbRunRecorder, track_run
from clin_compass.modules.proposals import service as proposals
from clin_compass.modules.proposals.schemas import OriginType, ProposalOut
from tests.conftest import MakeUser
from tests.proposals.test_proposals import KIND, login, register_patient

LIST = "/api/v1/proposals"
SUMMARY = "/api/v1/proposals/summary"
MOSCOW = timezone(timedelta(hours=3))


@dataclass(frozen=True)
class Clinic:
    """Врач А с двумя пациентами и пациент врача Б с похожей фамилией."""

    doctor_a: UUID
    doctor_b: UUID
    ivanova: UUID
    petrov: UUID
    foreign: UUID


@pytest.fixture
async def clinic(client: httpx.AsyncClient, make_user: MakeUser) -> Clinic:
    doctor_a = await make_user(email="a@example.com")
    doctor_b = await make_user(email="b@example.com")
    ivanova = await register_patient(
        client, "a@example.com", "iv@example.com", "Иванова Анна Сергеевна"
    )
    petrov = await register_patient(client, "a@example.com", "pe@example.com", "Петров Олег Ильич")
    foreign = await register_patient(
        client, "b@example.com", "fo@example.com", "Иванов Борис Петрович"
    )
    return Clinic(doctor_a.id, doctor_b.id, ivanova, petrov, foreign)


async def add(
    maker: async_sessionmaker[AsyncSession],
    patient_id: UUID,
    count: int = 1,
    *,
    origin_type: OriginType = "rule",
    origin_ref: str = "test-rule-1",
) -> list[ProposalOut]:
    """`count` предложений пациента одной транзакцией: `created_at` у них одно (now())."""
    async with maker() as session:
        made = [
            await proposals.create_proposal(
                session,
                patient_id=patient_id,
                kind=KIND,
                payload={"n": n},
                origin_type=origin_type,
                origin_ref=origin_ref,
            )
            for n in range(count)
        ]
        await session.commit()
    return made


async def decide(
    maker: async_sessionmaker[AsyncSession],
    doctor_id: UUID,
    proposal: ProposalOut,
    *,
    confirm: bool,
    comment: str | None = None,
) -> None:
    async def apply(_: AsyncSession, __: ProposalOut) -> None:
        return None

    registry = proposals.ApplierRegistry()
    registry.register(KIND, apply)
    async with maker() as session:
        await proposals.decide(
            session,
            registry,
            doctor_id=doctor_id,
            proposal_id=proposal.id,
            confirm=confirm,
            comment=comment,
        )
        await session.commit()


async def page(client: httpx.AsyncClient, **params: Any) -> dict[str, Any]:
    response = await client.get(LIST, params=params)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def ids(body: dict[str, Any]) -> list[str]:
    return [item["id"] for item in body["items"]]


async def test_queue_lists_own_patients_newest_first_with_names(
    client: httpx.AsyncClient, sessionmaker: async_sessionmaker[AsyncSession], clinic: Clinic
) -> None:
    [older] = await add(sessionmaker, clinic.ivanova)
    [newer] = await add(sessionmaker, clinic.petrov)
    await add(sessionmaker, clinic.foreign)
    await login(client, "a@example.com")

    body = await page(client, status="proposed")

    assert [(i["id"], i["patient_name"]) for i in body["items"]] == [
        (str(newer.id), "Петров Олег Ильич"),
        (str(older.id), "Иванова Анна Сергеевна"),
    ]
    assert body["items"][0]["payload"] == {"n": 0}
    assert body["next_cursor"] is None


async def test_patient_cannot_read_queue(client: httpx.AsyncClient, clinic: Clinic) -> None:
    await login(client, "iv@example.com")

    listed = await client.get(LIST)
    summary = await client.get(SUMMARY, params={"since": datetime.now(UTC).isoformat()})

    assert [listed.status_code, summary.status_code] == [403, 403]


async def test_queue_filters_by_status(
    client: httpx.AsyncClient, sessionmaker: async_sessionmaker[AsyncSession], clinic: Clinic
) -> None:
    waiting, confirmed, rejected = await add(sessionmaker, clinic.ivanova, 3)
    await decide(sessionmaker, clinic.doctor_a, confirmed, confirm=True)
    await decide(sessionmaker, clinic.doctor_a, rejected, confirm=False, comment="Нет показаний")
    await login(client, "a@example.com")

    by_status = {s: await page(client, status=s) for s in ("proposed", "confirmed", "rejected")}

    assert ids(by_status["proposed"]) == [str(waiting.id)]
    assert ids(by_status["confirmed"]) == [str(confirmed.id)]
    assert ids(by_status["rejected"]) == [str(rejected.id)]
    assert by_status["rejected"]["items"][0]["decision_comment"] == "Нет показаний"
    assert ids(await page(client)) == [str(waiting.id)]


async def test_queue_searches_name_of_own_patients_ignoring_case(
    client: httpx.AsyncClient, sessionmaker: async_sessionmaker[AsyncSession], clinic: Clinic
) -> None:
    [hers] = await add(sessionmaker, clinic.ivanova)
    await add(sessionmaker, clinic.petrov)
    await add(sessionmaker, clinic.foreign)
    await login(client, "a@example.com")

    # «ИВАН» есть и у пациента врача Б: в ответ он не попадает.
    assert ids(await page(client, q="ИВАН")) == [str(hers.id)]
    assert ids(await page(client, q="анна серг")) == [str(hers.id)]
    assert ids(await page(client, q="Сидоров")) == []


async def test_queue_search_treats_yo_as_ye(
    client: httpx.AsyncClient, sessionmaker: async_sessionmaker[AsyncSession], clinic: Clinic
) -> None:
    semyonova = await register_patient(
        client, "a@example.com", "se@example.com", "Семёнова Ольга Сергеевна"
    )
    [hers] = await add(sessionmaker, semyonova)
    [his] = await add(sessionmaker, clinic.petrov)
    await login(client, "a@example.com")

    assert ids(await page(client, q="Семенова")) == [str(hers.id)]
    assert ids(await page(client, q="ПЁТРОВ")) == [str(his.id)]


async def test_doctor_own_actions_are_not_in_queue_or_summary(
    client: httpx.AsyncClient, sessionmaker: async_sessionmaker[AsyncSession], clinic: Clinic
) -> None:
    # `origin_type = "doctor"` — действие самого врача (отмена назначения, изменённая схема):
    # решать в очереди нечего.
    [waiting] = await add(sessionmaker, clinic.ivanova)
    own = await add(
        sessionmaker, clinic.ivanova, 2, origin_type="doctor", origin_ref=str(clinic.doctor_a)
    )
    await decide(sessionmaker, clinic.doctor_a, own[1], confirm=True)
    await login(client, "a@example.com")
    since = datetime.now(MOSCOW) - timedelta(hours=1)

    summary = await client.get(SUMMARY, params={"since": since.isoformat()})

    assert ids(await page(client)) == [str(waiting.id)]
    assert ids(await page(client, status="confirmed")) == []
    assert summary.json() == {"pending": 1, "decided_today": 0}


async def test_pages_have_no_repeats_or_gaps_when_created_at_is_equal(
    client: httpx.AsyncClient, sessionmaker: async_sessionmaker[AsyncSession], clinic: Clinic
) -> None:
    same = await add(sessionmaker, clinic.ivanova, 5)
    [newest] = await add(sessionmaker, clinic.petrov)
    assert len({p.created_at for p in same}) == 1
    await login(client, "a@example.com")

    seen: list[str] = []
    sizes: list[int] = []
    cursor: str | None = None
    while True:
        body = await page(client, limit=2, **({"cursor": cursor} if cursor else {}))
        seen += ids(body)
        sizes.append(len(body["items"]))
        cursor = body["next_cursor"]
        if cursor is None:
            break

    assert sizes == [2, 2, 2]
    assert seen[0] == str(newest.id)
    assert sorted(seen) == sorted(str(p.id) for p in [*same, newest])


async def test_queue_rejects_limit_above_100_and_broken_cursor(
    client: httpx.AsyncClient, clinic: Clinic
) -> None:
    await login(client, "a@example.com")

    too_many = await client.get(LIST, params={"limit": 101})
    broken = await client.get(LIST, params={"cursor": "не курсор"})

    assert [too_many.status_code, broken.status_code] == [422, 422]


async def test_queue_rejects_cursor_without_timezone(
    client: httpx.AsyncClient, clinic: Clinic
) -> None:
    await login(client, "a@example.com")
    naive = proposals._encode_cursor(datetime(2026, 10, 23, 10, 0), uuid4())

    response = await client.get(LIST, params={"cursor": naive})

    assert response.status_code == 422


async def access_log(maker: async_sessionmaker[AsyncSession]) -> list[tuple[object, ...]]:
    async with maker() as session:
        rows = await session.execute(
            text(
                "SELECT actor_id, action, patient_id FROM identity.access_log "
                "WHERE action LIKE 'proposals.%' ORDER BY id"
            )
        )
        return [tuple(row) for row in rows]


async def test_queue_logs_access_to_each_patient_in_response(
    client: httpx.AsyncClient, sessionmaker: async_sessionmaker[AsyncSession], clinic: Clinic
) -> None:
    await add(sessionmaker, clinic.ivanova, 2)
    await add(sessionmaker, clinic.petrov)
    await add(sessionmaker, clinic.foreign)
    await login(client, "a@example.com")

    await page(client)

    logged = await access_log(sessionmaker)
    assert sorted(logged, key=str) == sorted(
        [
            (clinic.doctor_a, "proposals.list_proposed", clinic.ivanova),
            (clinic.doctor_a, "proposals.list_proposed", clinic.petrov),
        ],
        key=str,
    )


async def test_empty_queue_logs_one_request_without_patient(
    client: httpx.AsyncClient, sessionmaker: async_sessionmaker[AsyncSession], clinic: Clinic
) -> None:
    await login(client, "a@example.com")

    await page(client, status="rejected")

    assert await access_log(sessionmaker) == [(clinic.doctor_a, "proposals.list_rejected", None)]


async def test_queue_item_tells_which_run_made_it(
    client: httpx.AsyncClient, sessionmaker: async_sessionmaker[AsyncSession], clinic: Clinic
) -> None:
    async with track_run(
        DbRunRecorder(sessionmaker),
        role="llm",
        task="freetext.extract",
        model="qwen3.6",
        input_hash="0" * 64,
        prompt_version="extract-7a3603ce",
    ) as run:
        pass
    async with sessionmaker() as session:
        row = await session.get(AiRun, run.run_id)
    assert row is not None
    [by_run] = await add(sessionmaker, clinic.ivanova, origin_type="ai_run", origin_ref=str(row.id))
    [lost] = await add(sessionmaker, clinic.ivanova, origin_type="ai_run", origin_ref=str(uuid4()))
    [odd] = await add(sessionmaker, clinic.ivanova, origin_type="ai_run", origin_ref="not-a-uuid")
    [rule] = await add(sessionmaker, clinic.ivanova, origin_type="rule", origin_ref=str(row.id))
    await login(client, "a@example.com")

    runs = {item["id"]: item["run"] for item in (await page(client))["items"]}

    made = runs[str(by_run.id)]
    assert (made["model"], made["prompt_version"]) == ("qwen3.6", "extract-7a3603ce")
    assert datetime.fromisoformat(made["created_at"]) == row.created_at
    assert [runs[str(p.id)] for p in (lost, odd, rule)] == [None, None, None]


async def test_summary_counts_waiting_and_decided_since_given_moment(
    client: httpx.AsyncClient, sessionmaker: async_sessionmaker[AsyncSession], clinic: Clinic
) -> None:
    _, confirmed, rejected = await add(sessionmaker, clinic.ivanova, 3)
    await decide(sessionmaker, clinic.doctor_a, confirmed, confirm=True)
    await decide(sessionmaker, clinic.doctor_a, rejected, confirm=False)
    foreign = await add(sessionmaker, clinic.foreign, 2)
    await decide(sessionmaker, clinic.doctor_b, foreign[0], confirm=False)
    await login(client, "a@example.com")
    now = datetime.now(MOSCOW)

    async def summary(since: datetime) -> dict[str, int]:
        response = await client.get(SUMMARY, params={"since": since.isoformat()})
        assert response.status_code == 200, response.text
        body: dict[str, int] = response.json()
        return body

    assert await summary(now - timedelta(hours=1)) == {"pending": 1, "decided_today": 2}
    assert await summary(now + timedelta(hours=1)) == {"pending": 1, "decided_today": 0}


async def test_summary_requires_timezone_in_since(
    client: httpx.AsyncClient, clinic: Clinic
) -> None:
    await login(client, "a@example.com")

    response = await client.get(SUMMARY, params={"since": "2026-10-23T00:00:00"})

    assert response.status_code == 422
