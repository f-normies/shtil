"""Очередь задач на Postgres: PgQueuer в схеме `pgq`.

Задача ставится через то же соединение, что и бизнес-запись, поэтому коммитится
или откатывается вместе с ней. Упавшая задача повторяется с растущей паузой;
после последней попытки остаётся в очереди со статусом `failed`.

Обработчик может сам отложить задачу: `raise RetryRequested(delay=…)`. Такой повтор никогда
не становится последним — так задача ждёт недоступный узел модели сколько угодно. Но счётчик
попыток он увеличивает: обычная ошибка после этого может сразу оказаться последней. Поэтому
обработчик, который ждёт через RetryRequested, сам считает неудачи и обычных ошибок не выпускает.
"""

import asyncio
import contextlib
import dataclasses
import time
from collections.abc import Awaitable, Callable, Collection, Mapping
from datetime import UTC, datetime, timedelta

import asyncpg
import structlog
from pgqueuer import PgQueuer
from pgqueuer.adapters.persistence.qb import (
    QueryBuilderEnvironment,
    QueryQueueBuilder,
    QuerySchedulerBuilder,
)
from pgqueuer.adapters.persistence.queries import Queries
from pgqueuer.core.executors import (
    AbstractEntrypointExecutor,
    DatabaseRetryEntrypointExecutor,
    EntrypointExecutorParameters,
)
from pgqueuer.db import AsyncpgDriver
from pgqueuer.domain.errors import retry_request
from pgqueuer.domain.settings import DBSettings

# Реэкспорт: обработчик задачи откладывает её сам (см. описание модуля).
from pgqueuer.errors import RetryRequested as RetryRequested
from pgqueuer.models import Job
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.core import metrics

QUEUE_SCHEMA = "pgq"
# Имя соединения worker'а в pg_stat_activity: видно в мониторинге и тестах.
WORKER_APPLICATION_NAME = "compass-worker"

log = structlog.get_logger()

Handler = Callable[[bytes | None], Awaitable[None]]
# Состояния, которые count_jobs отдаёт и без задач: ждёт и выполняется.
_ALWAYS_COUNTED = ("queued", "picked")


@dataclasses.dataclass(frozen=True)
class Entrypoint:
    """Обработчик с лимитом одновременных задач. Лимит общий для всех процессов worker'а."""

    handler: Handler
    # 0 — без лимита.
    concurrency_limit: int = 0


@dataclasses.dataclass(frozen=True)
class WorkerSettings:
    # Всего попыток, включая первую.
    max_attempts: int = 5
    initial_retry_delay: timedelta = timedelta(seconds=2)
    max_retry_delay: timedelta = timedelta(minutes=5)
    # Как часто worker сам проверяет очередь: нужно для задач, отложенных на повтор.
    poll_interval: timedelta = timedelta(seconds=5)


class WorkerStoppedError(Exception):
    """Worker остановился сам (например, потерял соединение с БД). Процесс нужно перезапустить."""


def _queries(driver: AsyncpgDriver) -> Queries:
    """Queries с явной схемой `pgq`: не зависим от PGQUEUER_SCHEMA в окружении."""
    settings = DBSettings(db_schema=QUEUE_SCHEMA)
    return Queries(
        driver,
        qbe=QueryBuilderEnvironment(settings),
        qbq=QueryQueueBuilder(settings),
        qbs=QuerySchedulerBuilder(settings),
    )


async def enqueue(
    session: AsyncSession,
    entrypoint: str,
    payload: bytes | None = None,
    *,
    priority: int = 0,
    dedupe_key: str | None = None,
    execute_after: timedelta | None = None,
) -> int | None:
    """Ставит задачу в транзакции сессии. Видна worker'у только после коммита.

    Если задача с тем же `dedupe_key` уже ждёт или выполняется, новая не ставится
    и возвращается None. Бизнес-транзакция при этом не ломается. `execute_after` —
    не раньше чем через этот промежуток от постановки.
    """
    connection = await session.connection()
    # Адаптер asyncpg в SQLAlchemy открывает транзакцию лениво — на первой команде.
    # Без этой команды задача, поставленная до любой записи, закоммитилась бы сразу.
    await connection.exec_driver_sql("SELECT 1")
    raw = await connection.get_raw_connection()
    driver = AsyncpgDriver(raw.driver_connection)
    job_ids = await _queries(driver).enqueue(
        entrypoint,
        payload,
        priority,
        execute_after=execute_after,
        dedupe_key=dedupe_key,
        on_conflict="skip",
    )
    job_id = job_ids[0]
    return int(job_id) if job_id is not None else None


def build_worker(
    connection: asyncpg.Connection,
    handlers: Mapping[str, Handler | Entrypoint],
    settings: WorkerSettings,
) -> PgQueuer:
    """PgQueuer с обработчиками: повторы с растущей паузой, после последней — статус failed."""
    driver = AsyncpgDriver(connection)
    worker = PgQueuer(driver, queries=_queries(driver))

    def retrying(parameters: EntrypointExecutorParameters) -> AbstractEntrypointExecutor:
        return DatabaseRetryEntrypointExecutor(
            parameters=parameters,
            # У PgQueuer max_attempts — число повторов после первой попытки.
            max_attempts=settings.max_attempts - 1,
            initial_delay=settings.initial_retry_delay,
            max_delay=settings.max_retry_delay,
        )

    for name, handler in handlers.items():
        entry = handler if isinstance(handler, Entrypoint) else Entrypoint(handler)
        worker.entrypoint(
            name,
            concurrency_limit=entry.concurrency_limit,
            on_failure="hold",
            executor_factory=retrying,
        )(_job_runner(name, entry.handler, settings.max_attempts))
    return worker


def _job_runner(
    entrypoint: str, handler: Handler, max_attempts: int
) -> Callable[[Job], Awaitable[None]]:
    """Обработчик задачи с замером: время попытки и её исход, ожидание, сквозное время.

    Ожидание меряется от постановки задачи (`Job.created`) и только у первой попытки:
    у повтора время от постановки включало бы прошлые попытки и паузу перед повтором.
    Сквозное время — от постановки до успешного завершения, с повторами и паузами.
    Исход `retry` — обработчик сам отложил задачу (RetryRequested); любая другая ошибка —
    `error`, даже если PgQueuer её повторит. Отмена (остановка worker'а) не считается.
    Ошибка последней попытки — `queue.task_failed` в лог: дальше задача только `failed`.
    """

    async def run(job: Job) -> None:
        if job.attempts == 0:
            metrics.TASK_WAIT_SECONDS.labels(entrypoint=entrypoint).observe(_since(job.created))
        started = time.perf_counter()
        try:
            await handler(job.payload)
        except Exception as exc:
            outcome = "retry" if retry_request(exc) is not None else "error"
            _observe_task(entrypoint, outcome, started)
            # Условие последней попытки — как у DatabaseRetryEntrypointExecutor.
            if outcome == "error" and job.attempts >= max_attempts - 1:
                log.exception("queue.task_failed", entrypoint=entrypoint, job_id=job.id)
            raise
        _observe_task(entrypoint, "ok", started)
        metrics.TASK_LATENCY_SECONDS.labels(entrypoint=entrypoint).observe(_since(job.created))

    return run


def _since(moment: datetime) -> float:
    """Секунды от `moment` (время БД) до сейчас; расхождение часов не даёт отрицательных."""
    return max((datetime.now(UTC) - moment).total_seconds(), 0.0)


def _observe_task(entrypoint: str, outcome: str, started: float) -> None:
    seconds = time.perf_counter() - started
    metrics.TASK_SECONDS.labels(entrypoint=entrypoint, outcome=outcome).observe(seconds)


async def count_jobs(
    sessionmaker: async_sessionmaker[AsyncSession], entrypoints: Collection[str] = ()
) -> dict[tuple[str, str], int]:
    """Задачи в таблице очереди по `entrypoint` и состоянию, одним запросом.

    PgQueuer удаляет выполненные задачи, поэтому у пустой очереди рядов нет. У `entrypoints`
    без задач в состояниях `queued` и `picked` возвращается 0: на панели число, а не пусто.
    """
    table = DBSettings(db_schema=QUEUE_SCHEMA).qualified.queue_table
    counts = {(name, status): 0 for name in entrypoints for status in _ALWAYS_COUNTED}
    async with sessionmaker() as session:
        rows = await session.execute(
            text(f"SELECT entrypoint, status::text, count(*) FROM {table} GROUP BY 1, 2")
        )
        counts |= {(entrypoint, status): int(count) for entrypoint, status, count in rows}
    return counts


def asyncpg_dsn(database_url: str) -> str:
    """URL SQLAlchemy (`postgresql+asyncpg://…`) → DSN для asyncpg (`postgresql://…`)."""
    return make_url(database_url).set(drivername="postgresql").render_as_string(hide_password=False)


async def run_worker(
    database_url: str,
    handlers: Mapping[str, Handler | Entrypoint],
    stop: asyncio.Event,
    settings: WorkerSettings | None = None,
) -> None:
    """Обрабатывает задачи, пока не выставлен `stop`.

    Если worker остановился сам (обрыв соединения с БД и т. п.), бросает
    WorkerStoppedError: процесс должен выйти с ошибкой, чтобы его перезапустили.
    """
    resolved = settings or WorkerSettings()
    connection = await asyncpg.connect(
        asyncpg_dsn(database_url), server_settings={"application_name": WORKER_APPLICATION_NAME}
    )
    worker = build_worker(connection, handlers, resolved)
    running = asyncio.create_task(
        worker.run(dequeue_timeout=resolved.poll_interval, shutdown_on_listener_failure=True)
    )
    stopping = asyncio.create_task(stop.wait())
    try:
        await asyncio.wait({running, stopping}, return_when=asyncio.FIRST_COMPLETED)
        if running.done():
            raise WorkerStoppedError("PgQueuer stopped on its own") from running.exception()
        worker.shutdown.set()
        await running
    finally:
        stopping.cancel()
        with contextlib.suppress(asyncpg.InterfaceError, OSError):
            await connection.close()
