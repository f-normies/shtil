"""API с заданными лимитами частоты и клиенты с разных IP (спека #23).

`ASGITransport` по умолчанию приходит с 127.0.0.1: клиент «с другого IP» создаётся явно.
"""

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

import httpx
from asgi_lifespan import LifespanManager

from clin_compass.core.config import Settings
from clin_compass.main import create_app

ClientFrom = Callable[[str], httpx.AsyncClient]

THRESHOLDS = (
    "login_failures_per_pair",
    "login_failures_per_ip",
    "login_failures_per_account",
    "invites_per_doctor",
    "registration_failures_per_ip",
)


@asynccontextmanager
async def limited_api(
    postgres_url: str, *, trusted_proxies: tuple[str, ...] = (), **limits: int
) -> AsyncIterator[ClientFrom]:
    """Запущенный API с лимитами `limits`; отдаёт фабрику клиентов по IP.

    Пороги, не заданные в `limits`, — значения по умолчанию из `Settings`. Все пороги, секрет
    и доверенные прокси передаются явно: Makefile экспортирует .env разработчика, там они могут
    быть изменены.
    """
    unknown = limits.keys() - set(THRESHOLDS)
    if unknown:
        raise TypeError(f"неизвестные пороги: {sorted(unknown)}")
    thresholds = {name: Settings.model_fields[name].default for name in THRESHOLDS} | limits
    settings = Settings(
        database_url=postgres_url,
        environment="test",
        knowledge_dir=None,
        llm_urls=(),
        embeddings_urls=(),
        asr_urls=(),
        throttle_secret=None,
        trusted_proxies=trusted_proxies,
        s3_endpoint_url=None,
        s3_access_key=None,
        s3_secret_key=None,
        s3_bucket=None,
        s3_region=None,
        **thresholds,
    )
    clients: list[httpx.AsyncClient] = []
    async with LifespanManager(create_app(settings)) as manager:

        def client_from(ip: str) -> httpx.AsyncClient:
            client = httpx.AsyncClient(
                transport=httpx.ASGITransport(app=manager.app, client=(ip, 50000)),
                base_url="http://test",
            )
            clients.append(client)
            return client

        try:
            yield client_from
        finally:
            for client in clients:
                await client.aclose()
