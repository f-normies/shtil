"""Worker отдаёт метрики на своём порту и считает задачи в очереди (спека #40, 3.4)."""

import asyncio

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass import worker
from clin_compass.core import queue
from clin_compass.core.config import Settings
from tests.core.test_metrics import free_port, scrape

QUEUED = 'compass_queue_jobs{entrypoint="test.unhandled",status="queued"} 2.0'


async def test_worker_serves_queue_length_on_metrics_port(
    settings: Settings, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    async with sessionmaker() as session:
        for n in range(2):
            await queue.enqueue(session, "test.unhandled", str(n).encode())
        await session.commit()
    # Зарегистрированная задача без рядов в очереди: на панели 0, а не «No data».
    registered = sorted(worker.build_handlers(settings, sessionmaker))[0]
    lines = [
        QUEUED,
        f'compass_queue_jobs{{entrypoint="{registered}",status="queued"}} 0.0',
        f'compass_queue_jobs{{entrypoint="{registered}",status="picked"}} 0.0',
    ]
    port = free_port()
    stop = asyncio.Event()
    running = asyncio.create_task(
        worker.run(settings.model_copy(update={"metrics_port": port}), stop)
    )

    def scraped() -> str:
        try:
            return scrape(port)
        except OSError:
            return ""

    text = ""
    try:
        async with asyncio.timeout(10):
            while not all(line in text for line in lines):
                await asyncio.sleep(0.05)
                text = await asyncio.to_thread(scraped)
    finally:
        stop.set()
        await asyncio.wait_for(running, timeout=10)

    assert await asyncio.to_thread(scraped) == ""
