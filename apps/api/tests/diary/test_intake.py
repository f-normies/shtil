"""Отметки о приёме хранят историю и снимок схемы на дату."""

from datetime import date, datetime
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.modules.proposals import service as proposals
from clin_compass.modules.therapy.models import Prescription
from tests.diary.conftest import DiaryCare, create_care, login

TZ = ZoneInfo("Europe/Moscow")


async def add_prescription(
    sessionmaker: async_sessionmaker[AsyncSession], care: DiaryCare, *, started_on: date
) -> UUID:
    async with sessionmaker() as session:
        proposal = await proposals.create_proposal(
            session,
            patient_id=care.patient.id,
            kind="therapy.prescription",
            payload={"trade_name": "Синтетический препарат"},
            origin_type="rule",
            origin_ref="diary-test",
        )
        row = Prescription(
            patient_id=care.patient.id,
            drug_id="synthetic-drug",
            indication_id="synthetic-indication",
            dose_mg=25.0,
            times_per_day=2,
            schedule="утром и вечером",
            duration_days=None,
            off_label=False,
            off_label_reason=None,
            card_snapshot={"trade_name": "Синтетический препарат", "inn": "синтетическое МНН"},
            proposal_id=proposal.id,
            confirmed_by=care.doctor.id,
            started_at=datetime.combine(started_on, datetime.min.time(), TZ),
        )
        session.add(row)
        await session.flush()
        prescription_id = row.id
        await session.commit()
    return prescription_id


async def test_taken_then_unmarked_preserves_versions_and_scheme_snapshot(
    client: httpx.AsyncClient,
    diary_care: DiaryCare,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    today = datetime.now(TZ).date()
    prescription_id = await add_prescription(sessionmaker, diary_care, started_on=today)
    await login(client, diary_care.patient.email)
    taken = await client.post(
        "/api/v1/diary/intake",
        json={
            "operation_id": str(uuid4()),
            "prescription_id": str(prescription_id),
            "scheduled_on": today.isoformat(),
            "dose_ordinal": 1,
            "status": "taken",
            "occurred_at": datetime.now(TZ).replace(microsecond=0).isoformat(),
        },
    )
    unmarked = await client.post(
        "/api/v1/diary/intake",
        json={
            "operation_id": str(uuid4()),
            "prescription_id": str(prescription_id),
            "scheduled_on": today.isoformat(),
            "dose_ordinal": 1,
            "status": "unmarked",
            "occurred_at": datetime.now(TZ).replace(microsecond=0).isoformat(),
        },
    )
    current = await client.get("/api/v1/diary/intake", params={"scheduled_on": today.isoformat()})
    async with sessionmaker() as session:
        history = (
            await session.execute(
                text(
                    "SELECT version, status, scheme_snapshot "
                    "FROM diary.intake_marks WHERE prescription_id = :id "
                    "ORDER BY version"
                ),
                {"id": prescription_id},
            )
        ).all()

    assert taken.status_code == 201
    assert unmarked.status_code == 201
    assert current.status_code == 200
    assert len(current.json()["items"]) == 1
    assert current.json()["items"][0]["status"] == "unmarked"
    assert current.json()["items"][0]["version"] == 2
    assert [row.version for row in history] == [1, 2]
    assert [row.status for row in history] == ["taken", "unmarked"]
    assert history[0].scheme_snapshot["times_per_day"] == 2
    assert history[0].scheme_snapshot["schedule"] == "утром и вечером"
    assert "dose_time" not in history[0].scheme_snapshot


async def test_taken_intake_is_counted_in_doctor_adherence(
    client: httpx.AsyncClient,
    diary_care: DiaryCare,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    today = datetime.now(TZ).date()
    prescription_id = await add_prescription(sessionmaker, diary_care, started_on=today)
    await login(client, diary_care.patient.email)
    response = await client.post(
        "/api/v1/diary/intake",
        json={
            "operation_id": str(uuid4()),
            "prescription_id": str(prescription_id),
            "scheduled_on": today.isoformat(),
            "dose_ordinal": 1,
            "status": "taken",
            "occurred_at": datetime.now(TZ).replace(microsecond=0).isoformat(),
        },
    )
    assert response.status_code == 201

    client.cookies.clear()
    await login(client, diary_care.doctor.email)
    adherence = await client.get(f"/api/v1/patients/{diary_care.patient.id}/adherence")

    assert adherence.status_code == 200
    body = adherence.json()
    assert body["taken"] == 1
    today_doses = next(item["doses"] for item in body["days"] if item["day"] == today.isoformat())
    assert next(item["status"] for item in today_doses if item["slot"] == 0) == "taken"

    client.cookies.clear()
    await login(client, diary_care.patient.email)
    cleared = await client.post(
        "/api/v1/diary/intake",
        json={
            "operation_id": str(uuid4()),
            "prescription_id": str(prescription_id),
            "scheduled_on": today.isoformat(),
            "dose_ordinal": 1,
            "status": "unmarked",
            "occurred_at": datetime.now(TZ).replace(microsecond=0).isoformat(),
        },
    )
    assert cleared.status_code == 201
    patient_doses = await client.get("/api/v1/me/doses", params={"day": today.isoformat()})
    assert (
        next(item["status"] for item in patient_doses.json()["doses"] if item["slot"] == 0) is None
    )

    client.cookies.clear()
    await login(client, diary_care.doctor.email)
    cleared_adherence = await client.get(f"/api/v1/patients/{diary_care.patient.id}/adherence")
    assert (cleared_adherence.json()["taken"], cleared_adherence.json()["missed"]) == (0, 0)


async def test_intake_rejects_ordinal_outside_prescription_snapshot(
    client: httpx.AsyncClient,
    diary_care: DiaryCare,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    today = datetime.now(TZ).date()
    prescription_id = await add_prescription(sessionmaker, diary_care, started_on=today)
    await login(client, diary_care.patient.email)

    response = await client.post(
        "/api/v1/diary/intake",
        json={
            "operation_id": str(uuid4()),
            "prescription_id": str(prescription_id),
            "scheduled_on": today.isoformat(),
            "dose_ordinal": 3,
            "status": "taken",
            "occurred_at": datetime.now(TZ).replace(microsecond=0).isoformat(),
        },
    )

    assert response.status_code == 409
    assert response.json()["code"] == "intake_scheme_conflict"


async def test_daily_revision_keeps_only_accepted_intake_versions_after_dose_changes(
    client: httpx.AsyncClient,
    diary_care: DiaryCare,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    today = datetime.now(TZ).date()
    own_prescription_id = await add_prescription(sessionmaker, diary_care, started_on=today)
    foreign = await create_care(
        sessionmaker, "snapshot-doctor@example.com", "snapshot-patient@example.com"
    )
    foreign_prescription_id = await add_prescription(sessionmaker, foreign, started_on=today)

    await login(client, foreign.patient.email)
    foreign_mark = await client.post(
        "/api/v1/diary/intake",
        json={
            "operation_id": str(uuid4()),
            "prescription_id": str(foreign_prescription_id),
            "scheduled_on": today.isoformat(),
            "dose_ordinal": 1,
            "status": "taken",
            "occurred_at": datetime.now(TZ).replace(microsecond=0).isoformat(),
        },
    )

    await login(client, diary_care.patient.email)
    first_mark = await client.post(
        "/api/v1/diary/intake",
        json={
            "operation_id": str(uuid4()),
            "prescription_id": str(own_prescription_id),
            "scheduled_on": today.isoformat(),
            "dose_ordinal": 1,
            "status": "taken",
            "occurred_at": datetime.now(TZ).replace(microsecond=0).isoformat(),
        },
    )
    daily_body = {
        "operation_id": str(uuid4()),
        "entry_id": str(uuid4()),
        "kind": "daily",
        "occurred_at": datetime.now(TZ).replace(microsecond=0).isoformat(),
        "daily_on": today.isoformat(),
        "mood_level": 3,
    }
    daily = await client.post("/api/v1/diary/entries", json=daily_body)

    async with sessionmaker() as session:
        prescription = await session.get(Prescription, own_prescription_id)
        assert prescription is not None
        prescription.dose_mg = 50.0
        prescription.times_per_day = 1
        prescription.schedule = "ежедневно"
        await session.commit()

    next_mark = await client.post(
        "/api/v1/diary/intake",
        json={
            "operation_id": str(uuid4()),
            "prescription_id": str(own_prescription_id),
            "scheduled_on": today.isoformat(),
            "dose_ordinal": 1,
            "status": "unmarked",
            "occurred_at": datetime.now(TZ).replace(microsecond=0).isoformat(),
        },
    )
    replay = await client.post("/api/v1/diary/entries", json=daily_body)
    patched_daily = await client.patch(
        f"/api/v1/diary/entries/{daily_body['entry_id']}",
        json={"operation_id": str(uuid4()), "expected_revision": 1, "mood_level": 4},
    )
    async with sessionmaker() as session:
        revision_ids = (
            (
                await session.execute(
                    text(
                        "SELECT id FROM diary.revisions WHERE entry_id = :entry_id "
                        "ORDER BY revision"
                    ),
                    {"entry_id": daily_body["entry_id"]},
                )
            )
            .scalars()
            .all()
        )
        links = (
            await session.execute(
                text(
                    "SELECT revisions.revision, marks.id, marks.version, marks.scheme_snapshot "
                    "FROM diary.revision_intake_marks AS links "
                    "JOIN diary.intake_marks AS marks ON marks.id = links.intake_mark_id "
                    "JOIN diary.revisions AS revisions ON revisions.id = links.revision_id "
                    "WHERE revisions.entry_id = :entry_id ORDER BY revisions.revision"
                ),
                {"entry_id": daily_body["entry_id"]},
            )
        ).all()
        histories = (
            await session.execute(
                text(
                    "SELECT version, scheme_snapshot FROM diary.intake_marks "
                    "WHERE patient_id = :patient_id AND prescription_id = :prescription_id "
                    "AND scheduled_on = :scheduled_on ORDER BY version"
                ),
                {
                    "patient_id": diary_care.patient.id,
                    "prescription_id": own_prescription_id,
                    "scheduled_on": today,
                },
            )
        ).all()

    assert foreign_mark.status_code == 201
    assert first_mark.status_code == 201
    assert daily.status_code == 201
    assert next_mark.status_code == 201
    assert replay.status_code == 200
    assert replay.json() == daily.json()
    assert patched_daily.status_code == 200
    assert len(revision_ids) == 2
    assert len(histories) == 2
    assert histories[0].scheme_snapshot["dose_mg"] == 25.0
    assert histories[1].scheme_snapshot["dose_mg"] == 50.0
    assert [
        (row.revision, row.id, row.version, row.scheme_snapshot["dose_mg"]) for row in links
    ] == [
        (1, UUID(first_mark.json()["id"]), 1, 25.0),
        (2, UUID(first_mark.json()["id"]), 1, 25.0),
    ]
