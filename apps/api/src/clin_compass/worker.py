"""Точка входа worker'а очереди: python -m clin_compass.worker.

Обработчики задач собирает build_handlers: модуль добавляет сюда свои задачи из tasks.py.
С COMPASS_METRICS_PORT worker отдаёт метрики на этом порту и раз в 15 с считает задачи
в очереди (спека #40, 3.4).
Если worker остановился сам (например, потерял БД), процесс выходит с кодом 1,
и его перезапускает оркестратор (`restart` в compose).
"""

import asyncio
import functools
import signal
import sys

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.core.config import Settings
from clin_compass.core.db import make_engine, make_sessionmaker
from clin_compass.core.logs import configure_logging
from clin_compass.core.metrics import poll_queue_jobs, serve_metrics
from clin_compass.core.queue import (
    Entrypoint,
    Handler,
    WorkerStoppedError,
    count_jobs,
    run_worker,
)
from clin_compass.modules.alerts import tasks as alerts
from clin_compass.modules.diary import tasks as diary
from clin_compass.modules.freetext import tasks as freetext
from clin_compass.modules.ingestion import tasks as ingestion
from clin_compass.modules.messaging import tasks as messaging
from clin_compass.modules.summary import tasks as summary
from clin_compass.modules.therapy import tasks as therapy

log = structlog.get_logger()


def build_handlers(
    settings: Settings, sessionmaker: async_sessionmaker[AsyncSession]
) -> dict[str, Handler | Entrypoint]:
    """Обработчики задач всех модулей.

    Без узлов LLM задачи разбора текста и сводки между визитами не регистрируются и ждут
    в очереди, пока узлы не настроят. Так же распознавание истории болезни ждёт узлов
    docparse, а извлечение из неё — узлов LLM. Справке по КР нужны ещё и узлы эмбеддингов.
    Ошибка в данных knowledge/ останавливает старт.
    """
    handlers: dict[str, Handler | Entrypoint] = {}
    handlers |= alerts.build_jobs(settings, sessionmaker).handlers()
    handlers |= messaging.build_jobs(settings, sessionmaker).handlers()
    handlers |= diary.build_jobs(settings, sessionmaker).handlers()
    if settings.llm_urls:
        handlers |= freetext.build_jobs(settings, sessionmaker).handlers()
        handlers |= summary.build_jobs(settings, sessionmaker).handlers()
    else:
        log.warning("worker.freetext_disabled", reason="COMPASS_LLM_URLS is empty")
        log.warning("worker.summary_disabled", reason="COMPASS_LLM_URLS is empty")
    handlers |= ingestion.build_jobs(settings, sessionmaker).handlers()
    if not settings.docparse_urls:
        log.warning("worker.ingestion_parse_disabled", reason="COMPASS_DOCPARSE_URLS is empty")
    if settings.llm_urls and settings.embeddings_urls:
        handlers |= therapy.build_jobs(settings, sessionmaker).handlers()
    else:
        log.warning(
            "worker.guidance_disabled",
            reason="COMPASS_LLM_URLS or COMPASS_EMBEDDINGS_URLS is empty",
        )
    return handlers


async def run(settings: Settings, stop: asyncio.Event) -> None:
    """Обрабатывает задачи, пока не выставлен `stop`; с портом метрик — отдаёт метрики.

    Если worker остановился сам, бросает WorkerStoppedError (см. `run_worker`).
    """
    engine = make_engine(settings.database_url)
    sessionmaker = make_sessionmaker(engine)
    try:
        handlers = build_handlers(settings, sessionmaker)
        log.info("worker.starting", entrypoints=sorted(handlers))
        with serve_metrics(settings.metrics_port) as port:
            if port is None:
                await run_worker(settings.database_url, handlers, stop)
                return
            # Зарегистрированные задачи видны на панели и при пустой очереди: с нулём.
            count = functools.partial(count_jobs, sessionmaker, entrypoints=tuple(handlers))
            polling = asyncio.create_task(poll_queue_jobs(count, stop))
            try:
                await run_worker(settings.database_url, handlers, stop)
            finally:
                polling.cancel()
                await asyncio.gather(polling, return_exceptions=True)
    finally:
        await engine.dispose()


async def main() -> None:
    settings = Settings()
    configure_logging(settings.environment)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    try:
        await run(settings, stop)
    except WorkerStoppedError:
        log.exception("worker.stopped_unexpectedly")
        sys.exit(1)
    log.info("worker.stopped")


if __name__ == "__main__":
    asyncio.run(main())
