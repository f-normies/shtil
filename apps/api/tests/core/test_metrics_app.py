"""Метрики на настоящем приложении (спека #40, 3.1, 3.4): шаблоны маршрутов, нет id и почты,
нет `/metrics` в API, сервер метрик живёт вместе с приложением."""

import re
from uuid import uuid4

import httpx
import pytest
from asgi_lifespan import LifespanManager
from prometheus_client import REGISTRY, generate_latest

from clin_compass.core.config import Settings
from clin_compass.main import create_app
from tests.core.test_metrics import free_port, http_count, scrape

UUID_PATTERN = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


async def test_route_label_is_full_template_of_api_route(client: httpx.AsyncClient) -> None:
    route = "/api/v1/alerts/{alert_id}/delivered"
    before = http_count("POST", route, "4xx")

    response = await client.post(f"/api/v1/alerts/{uuid4()}/delivered?via=ws")

    assert response.status_code == 401
    assert http_count("POST", route, "4xx") == before + 1


async def test_api_has_no_metrics_route(client: httpx.AsyncClient) -> None:
    before = http_count("GET", "unmatched", "4xx")

    response = await client.get("/metrics")

    assert response.status_code == 404
    assert http_count("GET", "unmatched", "4xx") == before + 1


async def test_health_is_not_counted(client: httpx.AsyncClient) -> None:
    before = sum(http_count("GET", "/api/v1/health", status) for status in ("2xx", "5xx"))

    await client.get("/api/v1/health")

    assert sum(http_count("GET", "/api/v1/health", status) for status in ("2xx", "5xx")) == before


async def test_metrics_output_has_no_ids_or_emails(client: httpx.AsyncClient) -> None:
    patient = uuid4()
    await client.post(
        "/api/v1/auth/login", json={"email": "doctor@example.com", "password": "wrong"}
    )
    await client.get(f"/api/v1/patients/{patient}")
    await client.get(f"/api/v1/patients/{patient}/alerts?email=doctor@example.com")
    await client.get(f"/api/v1/nothing/{patient}/doctor@example.com")

    text = generate_latest(REGISTRY).decode()

    assert 'route="/api/v1/patients/{patient_id}"' in text
    assert UUID_PATTERN.search(text) is None
    assert "@" not in text


async def test_app_serves_metrics_on_configured_port_while_running(settings: Settings) -> None:
    port = free_port()
    app = create_app(settings.model_copy(update={"metrics_port": port}))

    async with LifespanManager(app):
        text = scrape(port)

    assert "compass_http_request_duration_seconds" in text
    with pytest.raises(OSError):
        scrape(port)
