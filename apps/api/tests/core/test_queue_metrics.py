"""Метрики задач worker и очереди (спека #40, 3.1): время задачи, ожидание, длина очереди."""

import asyncio
from datetime import timedelta

from prometheus_client import REGISTRY
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.core import metrics, queue
from clin_compass.core.config import Settings
from tests.core.test_queue import FAST, wait_until, worker


def tasks(entrypoint: str, outcome: str) -> float:
    labels = {"entrypoint": entrypoint, "outcome": outcome}
    return REGISTRY.get_sample_value("compass_task_duration_seconds_count", labels) or 0.0


def waits(entrypoint: str) -> tuple[float, float]:
    labels = {"entrypoint": entrypoint}
    name = "compass_task_wait_seconds"
    return (
        REGISTRY.get_sample_value(f"{name}_count", labels) or 0.0,
        REGISTRY.get_sample_value(f"{name}_sum", labels) or 0.0,
    )


def latencies(entrypoint: str) -> tuple[float, float]:
    labels = {"entrypoint": entrypoint}
    name = "compass_task_latency_seconds"
    return (
        REGISTRY.get_sample_value(f"{name}_count", labels) or 0.0,
        REGISTRY.get_sample_value(f"{name}_sum", labels) or 0.0,
    )


def queue_jobs() -> dict[tuple[str, str], float]:
    [family] = metrics.QUEUE_JOBS.collect()
    return {(s.labels["entrypoint"], s.labels["status"]): s.value for s in family.samples}


async def enqueue(maker: async_sessionmaker[AsyncSession], entrypoint: str, count: int = 1) -> None:
    async with maker() as session:
        for n in range(count):
            await queue.enqueue(session, entrypoint, str(n).encode())
        await session.commit()


async def test_task_outcomes_are_observed(
    settings: Settings, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    calls: list[str] = []

    async def handler(payload: bytes | None) -> None:
        calls.append("call")
        if len(calls) == 1:
            raise queue.RetryRequested(delay=timedelta(milliseconds=20))
        if len(calls) == 2:
            raise RuntimeError("temporary failure")

    before = {outcome: tasks("test.outcomes", outcome) for outcome in ("ok", "retry", "error")}
    async with worker(settings, {"test.outcomes": handler}):
        await enqueue(sessionmaker, "test.outcomes")
        await wait_until(lambda: tasks("test.outcomes", "ok") > before["ok"])

    after = {outcome: tasks("test.outcomes", outcome) for outcome in ("ok", "retry", "error")}
    assert {k: after[k] - before[k] for k in after} == {"ok": 1, "retry": 1, "error": 1}


async def test_wait_is_measured_from_enqueue_once_per_job(
    settings: Settings, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    calls: list[str] = []

    async def handler(_: bytes | None) -> None:
        calls.append("call")
        if len(calls) == 1:
            raise queue.RetryRequested(delay=timedelta(milliseconds=20))

    count, total = waits("test.wait")
    await enqueue(sessionmaker, "test.wait")
    await asyncio.sleep(0.5)
    async with worker(settings, {"test.wait": handler}):
        await wait_until(lambda: len(calls) == 2)

    new_count, new_total = waits("test.wait")
    # Повтор после RetryRequested ожидание не добавляет: время от постановки включало бы
    # первую попытку и паузу перед повтором.
    assert new_count == count + 1
    assert 0.5 <= new_total - total < FAST.poll_interval.total_seconds() + 5


async def test_latency_spans_from_enqueue_to_success_with_retry_pause(
    settings: Settings, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    # Сквозное время справки по КР: очередь, все попытки и паузы между ними (спека #40, 3.2).
    calls: list[str] = []

    async def handler(_: bytes | None) -> None:
        calls.append("call")
        if len(calls) == 1:
            raise queue.RetryRequested(delay=timedelta(milliseconds=300))

    count, total = latencies("test.latency")
    await enqueue(sessionmaker, "test.latency")
    await asyncio.sleep(0.3)
    async with worker(settings, {"test.latency": handler}):
        await wait_until(lambda: latencies("test.latency")[0] > count)

    new_count, new_total = latencies("test.latency")
    assert new_count == count + 1
    # 0,3 с в очереди до первой попытки и 0,3 с паузы перед повтором.
    assert new_total - total >= 0.6


async def test_failed_job_has_no_latency(
    settings: Settings, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    async def broken(_: bytes | None) -> None:
        raise RuntimeError("always fails")

    count = latencies("test.no_latency")[0]
    errors = tasks("test.no_latency", "error")
    async with worker(settings, {"test.no_latency": broken}):
        await enqueue(sessionmaker, "test.no_latency")
        await wait_until(lambda: tasks("test.no_latency", "error") == errors + FAST.max_attempts)

    assert latencies("test.no_latency")[0] == count


async def test_queue_poll_sets_gauge_to_jobs_by_entrypoint_and_status(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await enqueue(sessionmaker, "test.poll.a", count=2)
    await enqueue(sessionmaker, "test.poll.b", count=3)
    async with sessionmaker() as session:
        await session.execute(
            text(
                "UPDATE pgq.pgqueuer SET status = 'failed' WHERE id = "
                "(SELECT min(id) FROM pgq.pgqueuer WHERE entrypoint = 'test.poll.b')"
            )
        )
        await session.commit()
    # Сочетание, которого в очереди уже нет, не должно висеть со старым числом.
    metrics.QUEUE_JOBS.labels(entrypoint="test.poll.gone", status="queued").set(7)

    stop = asyncio.Event()
    poll = asyncio.create_task(
        metrics.poll_queue_jobs(lambda: queue.count_jobs(sessionmaker), stop, interval=0.05)
    )
    await wait_until(lambda: ("test.poll.gone", "queued") not in queue_jobs())
    stop.set()
    await asyncio.wait_for(poll, timeout=5)

    assert queue_jobs() == {
        ("test.poll.a", "queued"): 2,
        ("test.poll.b", "queued"): 2,
        ("test.poll.b", "failed"): 1,
    }


async def test_registered_entrypoints_without_jobs_count_zero(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    # PgQueuer удаляет выполненные задачи: без нулей панель пустой очереди пишет «No data».
    await enqueue(sessionmaker, "test.busy")

    counts = await queue.count_jobs(sessionmaker, entrypoints=["test.idle", "test.busy"])

    assert counts == {
        ("test.idle", "queued"): 0,
        ("test.idle", "picked"): 0,
        ("test.busy", "queued"): 1,
        ("test.busy", "picked"): 0,
    }
