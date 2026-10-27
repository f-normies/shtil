"""Отзыв назначения одновременно с выдачей доступа на настоящем Postgres."""

import asyncio
from datetime import date
from uuid import UUID

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.modules.alerts import chain, deputies
from clin_compass.modules.alerts.models import Deputy
from clin_compass.modules.alerts.rules import RuleBook
from clin_compass.modules.identity.schemas import Role
from tests.alerts.conftest import Care
from tests.alerts.test_api import login
from tests.alerts.test_escalation_db import MINUTES, assign, grants, step, urgent, user


async def test_revoke_cannot_leave_a_grant_created_from_its_old_assignment(
    client: httpx.AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    rules: RuleBook,
    care: Care,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Выдача прочитала назначение, затем начался отзыв: после обоих коммитов карта закрыта.

    На старом коде отзыв коммитится между чтением и выдачей и пропускает новый доступ.
    Исправленный код задерживает отзыв общей блокировкой до коммита выдачи.
    """
    deputy = await user(sessionmaker, "deputy@example.com", Role.DOCTOR)
    await assign(sessionmaker, care, deputy)
    alert = await urgent(sessionmaker, rules, care)
    selected, release, revoked = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original = deputies.on_day

    async def pause_after_selection(
        session: AsyncSession, doctor_id: UUID, day: date
    ) -> Deputy | None:
        assignment = await original(session, doctor_id, day)
        selected.set()
        await release.wait()
        return assignment

    monkeypatch.setattr(deputies, "on_day", pause_after_selection)
    issuing = asyncio.create_task(step(sessionmaker, alert, 2, 30))
    revoking: asyncio.Task[None] | None = None
    try:
        async with asyncio.timeout(10):
            await selected.wait()
            async with sessionmaker() as session:
                assignment_id = await session.scalar(text("SELECT id FROM alerts.deputies"))
                admin_id = await session.scalar(
                    text("SELECT id FROM identity.users WHERE role = 'admin'")
                )
                backend = await session.scalar(text("SELECT pg_backend_pid()"))
                assert assignment_id is not None and admin_id is not None

                async def revoke() -> None:
                    await deputies.revoke(session, admin_id, assignment_id)
                    await session.commit()
                    revoked.set()

                revoking = asyncio.create_task(revoke())
                # Ждём наблюдаемого результата: старый код уже отозвал назначение,
                # новый ждёт блокировку выдачи. Фиксированной паузы для порядка нет.
                async with sessionmaker() as observer:
                    while not revoked.is_set():
                        blocked = await observer.scalar(
                            text("SELECT cardinality(pg_blocking_pids(:pid)) > 0"),
                            {"pid": backend},
                        )
                        if blocked:
                            break
                        await asyncio.sleep(0)
                release.set()
                await issuing
                await revoking
    finally:
        release.set()
        for task in (issuing, revoking):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(
            *(t for t in (issuing, revoking) if t is not None), return_exceptions=True
        )

    assert await grants(sessionmaker) == [(deputy.id, "revoked")]
    await login(client, "deputy@example.com")
    assert (await client.get(f"/api/v1/patients/{care.patient.id}")).status_code == 404
    assert (await client.post(f"/api/v1/alerts/{alert.id}/acknowledge")).status_code == 404


async def test_step_waits_for_an_uncommitted_revocation_before_selecting_a_deputy(
    sessionmaker: async_sessionmaker[AsyncSession], rules: RuleBook, care: Care
) -> None:
    """Отзыв начал первым: шаг 2 не выдаёт доступ по ещё видимой старой версии назначения."""
    deputy = await user(sessionmaker, "deputy@example.com", Role.DOCTOR)
    await assign(sessionmaker, care, deputy)
    alert = await urgent(sessionmaker, rules, care)
    assert alert.urgent_since is not None
    urgent_since = alert.urgent_since
    issued = asyncio.Event()
    async with sessionmaker() as revoking, sessionmaker() as issuing:
        assignment_id = await revoking.scalar(text("SELECT id FROM alerts.deputies"))
        admin_id = await revoking.scalar(text("SELECT id FROM identity.users WHERE role = 'admin'"))
        assert assignment_id is not None and admin_id is not None
        await deputies.revoke(revoking, admin_id, assignment_id)
        backend = await issuing.scalar(text("SELECT pg_backend_pid()"))

        async def issue() -> None:
            await chain.run(issuing, alert.id, 2, MINUTES, urgent_since)
            await issuing.commit()
            issued.set()

        task = asyncio.create_task(issue())
        try:
            async with asyncio.timeout(10), sessionmaker() as observer:
                while not issued.is_set():
                    if await observer.scalar(
                        text("SELECT cardinality(pg_blocking_pids(:pid)) > 0"), {"pid": backend}
                    ):
                        break
                    await asyncio.sleep(0)
                await revoking.commit()
                await task
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    assert await grants(sessionmaker) == []
