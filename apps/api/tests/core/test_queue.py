"""Очередь на Postgres: задача ставится в транзакции бизнес-записи (спека фундамента, 4.4)."""

import asyncio
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.core import queue
from clin_compass.core.config import Settings

# Быстрые повторы и частый опрос, чтобы тесты не ждали секундами.
FAST = queue.WorkerSettings(
    max_attempts=3,
    initial_retry_delay=timedelta(milliseconds=50),
    max_retry_delay=timedelta(milliseconds=200),
    poll_interval=timedelta(milliseconds=100),
)


async def queued_entrypoints(maker: async_sessionmaker[AsyncSession]) -> list[str]:
    async with maker() as session:
        rows = await session.scalars(text("SELECT entrypoint FROM pgq.pgqueuer ORDER BY id"))
        return list(rows)


async def job_statuses(maker: async_sessionmaker[AsyncSession]) -> list[str]:
    async with maker() as session:
        rows = await session.scalars(text("SELECT status::text FROM pgq.pgqueuer ORDER BY id"))
        return list(rows)


@asynccontextmanager
async def worker(
    settings: Settings, handlers: Mapping[str, queue.Handler]
) -> AsyncIterator[asyncio.Task[None]]:
    stop = asyncio.Event()
    task = asyncio.create_task(queue.run_worker(settings.database_url, handlers, stop, FAST))
    try:
        yield task
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=10)


async def wait_until(condition: Callable[[], object], limit: float = 10) -> None:
    async with asyncio.timeout(limit):
        while not condition():
            await asyncio.sleep(0.05)


async def test_enqueued_job_is_committed_with_transaction(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with sessionmaker() as session:
        await queue.enqueue(session, "test.echo", b"payload")
        await session.commit()

    assert await queued_entrypoints(sessionmaker) == ["test.echo"]


async def test_rolled_back_transaction_leaves_no_job(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with sessionmaker() as session:
        await queue.enqueue(session, "test.echo", b"payload")
        await session.rollback()

    assert await queued_entrypoints(sessionmaker) == []


async def test_job_is_invisible_to_others_until_commit(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with sessionmaker() as session:
        await queue.enqueue(session, "test.echo", b"payload")
        seen_before_commit = await queued_entrypoints(sessionmaker)
        await session.commit()

    assert seen_before_commit == []


async def test_duplicate_dedupe_key_does_not_break_business_transaction(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with sessionmaker() as session:
        first = await queue.enqueue(session, "test.echo", b"1", dedupe_key="same")
        second = await queue.enqueue(session, "test.echo", b"2", dedupe_key="same")
        # Бизнес-запись в той же транзакции должна пережить повтор ключа.
        await session.execute(text("CREATE TEMP TABLE marker (x int)"))
        await session.commit()

    assert first is not None
    assert second is None
    assert await queued_entrypoints(sessionmaker) == ["test.echo"]


async def test_delayed_job_waits_for_its_time(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    # Шаги эскалации (#56) ставятся заранее и ждут своего срока.
    async with sessionmaker() as session:
        await queue.enqueue(session, "test.echo", b"later", execute_after=timedelta(minutes=15))
        await session.commit()

    async with sessionmaker() as session:
        wait = await session.scalar(
            text("SELECT execute_after - now() FROM pgq.pgqueuer WHERE entrypoint = 'test.echo'")
        )
    assert timedelta(minutes=14) < wait <= timedelta(minutes=15)


async def test_worker_runs_registered_handler(
    settings: Settings, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    received: list[bytes | None] = []

    async def echo(payload: bytes | None) -> None:
        received.append(payload)

    async with worker(settings, {"test.echo": echo}):
        async with sessionmaker() as session:
            await queue.enqueue(session, "test.echo", b"hello")
            await session.commit()
        await wait_until(lambda: received)

    assert received == [b"hello"]


async def test_failing_job_is_retried_until_it_succeeds(
    settings: Settings, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    attempts: list[int] = []

    async def flaky(_: bytes | None) -> None:
        attempts.append(1)
        if len(attempts) < 3:
            raise RuntimeError("temporary failure")

    async with worker(settings, {"test.flaky": flaky}):
        async with sessionmaker() as session:
            await queue.enqueue(session, "test.flaky")
            await session.commit()
        await wait_until(lambda: len(attempts) >= 3)

    assert len(attempts) == 3


async def test_job_failing_every_attempt_ends_as_failed(
    settings: Settings, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    attempts: list[int] = []

    async def broken(_: bytes | None) -> None:
        attempts.append(1)
        raise RuntimeError("always fails")

    async with worker(settings, {"test.broken": broken}):
        async with sessionmaker() as session:
            await queue.enqueue(session, "test.broken")
            await session.commit()
        await wait_until(lambda: len(attempts) >= FAST.max_attempts)
        statuses: list[str] = []

        async def poll() -> bool:
            statuses[:] = await job_statuses(sessionmaker)
            return statuses == ["failed"]

        async with asyncio.timeout(10):
            while not await poll():
                await asyncio.sleep(0.05)

    assert len(attempts) == FAST.max_attempts
    assert statuses == ["failed"]


async def test_worker_reports_lost_database_connection(
    settings: Settings, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    stop = asyncio.Event()
    task = asyncio.create_task(queue.run_worker(settings.database_url, {}, stop, FAST))
    await asyncio.sleep(0.5)

    async with sessionmaker() as session:
        await session.execute(
            text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE application_name = :name"
            ),
            {"name": queue.WORKER_APPLICATION_NAME},
        )

    # Процесс не должен тихо зависнуть: run_worker завершается ошибкой,
    # и точка входа выходит с ненулевым кодом, чтобы её перезапустили.
    with pytest.raises(queue.WorkerStoppedError):
        await asyncio.wait_for(task, timeout=15)
    stop.set()


async def _max_parallel(
    settings: Settings,
    sessionmaker: async_sessionmaker[AsyncSession],
    concurrency_limit: int,
) -> int:
    """Сколько из трёх задач выполнялись одновременно."""
    active, peak, done = 0, 0, 0

    async def slow(_: bytes | None) -> None:
        nonlocal active, peak, done
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.3)
        active -= 1
        done += 1

    entry = queue.Entrypoint(slow, concurrency_limit=concurrency_limit)
    async with worker(settings, {"test.slow": entry}):
        async with sessionmaker() as session:
            for n in range(3):
                await queue.enqueue(session, "test.slow", str(n).encode())
            await session.commit()
        await wait_until(lambda: done == 3)
    return peak


async def test_jobs_without_limit_run_in_parallel(
    settings: Settings, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    # Контроль для теста лимита: без лимита задачи идут одновременно.
    assert await _max_parallel(settings, sessionmaker, concurrency_limit=0) > 1


async def test_concurrency_limit_runs_jobs_one_at_a_time(
    settings: Settings, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    # Узел LLM держит 2 запроса (ADR-0006): лимит точки входа не даёт задачам его перегрузить.
    assert await _max_parallel(settings, sessionmaker, concurrency_limit=1) == 1


async def test_retry_requested_is_never_the_last_attempt(
    settings: Settings, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    # Пока узел недоступен, задача ждёт сколько угодно: RetryRequested не завершает её ошибкой.
    calls: list[int] = []

    async def waiting(_: bytes | None) -> None:
        calls.append(1)
        if len(calls) <= FAST.max_attempts + 2:
            raise queue.RetryRequested(delay=timedelta(milliseconds=20))

    async with worker(settings, {"test.waiting": waiting}):
        async with sessionmaker() as session:
            await queue.enqueue(session, "test.waiting")
            await session.commit()
        await wait_until(lambda: len(calls) == FAST.max_attempts + 3)
        # Выполненная задача уходит из таблицы очереди; упавшая осталась бы с `failed`.
        async with asyncio.timeout(10):
            while await job_statuses(sessionmaker):
                await asyncio.sleep(0.05)

    assert len(calls) == FAST.max_attempts + 3


async def test_plain_error_after_retry_requests_fails_without_retry(
    settings: Settings, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    # RetryRequested увеличивает счётчик попыток. Обычная ошибка после исчерпания счётчика
    # сразу последняя: обработчик, который ждёт узел, не должен выпускать обычные ошибки.
    calls: list[int] = []

    async def waiting_then_broken(_: bytes | None) -> None:
        calls.append(1)
        if len(calls) <= FAST.max_attempts:
            raise queue.RetryRequested(delay=timedelta(milliseconds=20))
        raise RuntimeError("broken")

    async with worker(settings, {"test.mixed": waiting_then_broken}):
        async with sessionmaker() as session:
            await queue.enqueue(session, "test.mixed")
            await session.commit()
        async with asyncio.timeout(10):
            while await job_statuses(sessionmaker) != ["failed"]:
                await asyncio.sleep(0.05)

    assert len(calls) == FAST.max_attempts + 1
