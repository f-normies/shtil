"""Метрики Prometheus (спека #40, 3.1): отклик API по шаблону маршрута, сервер метрик, очередь.

Метрики живут в общем реестре процесса, поэтому тесты сравнивают значения до и после действия.
"""

import asyncio
import socket
import urllib.request
from collections.abc import AsyncIterator, Iterator
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI, WebSocket
from fastapi.responses import StreamingResponse
from prometheus_client import REGISTRY
from starlette.background import BackgroundTask
from starlette.testclient import TestClient
from structlog.testing import capture_logs

from clin_compass.core import metrics

HTTP = "compass_http_request_duration_seconds_count"
HEALTH = "/api/v1/health"


def sample(name: str, labels: dict[str, str]) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


def http_count(method: str, route: str, status: str) -> float:
    return sample(HTTP, {"method": method, "route": route, "status": status})


async def fail_after_response() -> None:
    raise RuntimeError("background failed")


def build_app() -> FastAPI:
    app = FastAPI()

    @app.get("/api/v1/stream")
    async def stream() -> StreamingResponse:
        async def chunks() -> AsyncIterator[bytes]:
            yield b"first"
            await asyncio.sleep(0.2)
            yield b"last"

        # Фоновая задача идёт после последнего байта ответа: в отклик она не входит.
        return StreamingResponse(chunks(), background=BackgroundTask(asyncio.sleep, 0.6))

    @app.get("/api/v1/late-error")
    async def late_error() -> StreamingResponse:
        async def chunks() -> AsyncIterator[bytes]:
            yield b"done"

        return StreamingResponse(chunks(), background=BackgroundTask(fail_after_response))

    @app.get("/api/v1/items/{item_id}")
    async def item(item_id: UUID) -> dict[str, str]:
        return {"id": str(item_id)}

    @app.get("/api/v1/boom")
    async def boom() -> None:
        raise RuntimeError("boom")

    @app.get(HEALTH)
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.websocket("/api/v1/ws")
    async def ws(websocket: WebSocket) -> None:
        await websocket.accept()
        await websocket.send_text("ready")
        await websocket.close()

    app.add_middleware(metrics.HttpMetricsMiddleware, skip_routes={HEALTH})
    return app


@pytest.fixture
def http() -> Iterator[TestClient]:
    with TestClient(build_app(), raise_server_exceptions=False) as client:
        yield client


def test_request_is_counted_by_route_template_and_status_class(http: TestClient) -> None:
    route = "/api/v1/items/{item_id}"
    ok_before = http_count("GET", route, "2xx")
    invalid_before = http_count("GET", route, "4xx")

    assert http.get(f"/api/v1/items/{uuid4()}").status_code == 200
    assert http.get("/api/v1/items/not-a-uuid").status_code == 422

    assert http_count("GET", route, "2xx") == ok_before + 1
    assert http_count("GET", route, "4xx") == invalid_before + 1


def test_unknown_path_is_counted_as_unmatched(http: TestClient) -> None:
    before = http_count("GET", "unmatched", "4xx")

    assert http.get(f"/api/v1/nothing/{uuid4()}").status_code == 404

    assert http_count("GET", "unmatched", "4xx") == before + 1


def test_unhandled_error_is_counted_as_5xx(http: TestClient) -> None:
    before = http_count("GET", "/api/v1/boom", "5xx")

    assert http.get("/api/v1/boom").status_code == 500

    assert http_count("GET", "/api/v1/boom", "5xx") == before + 1


def test_streaming_response_is_measured_to_its_last_byte(http: TestClient) -> None:
    labels = {"method": "GET", "route": "/api/v1/stream", "status": "2xx"}
    count = sample(HTTP, labels)
    total = sample("compass_http_request_duration_seconds_sum", labels)

    assert http.get("/api/v1/stream").content == b"firstlast"

    assert sample(HTTP, labels) == count + 1
    seconds = sample("compass_http_request_duration_seconds_sum", labels) - total
    assert 0.2 <= seconds < 0.6


def test_error_after_sent_response_keeps_its_status(http: TestClient) -> None:
    before = {status: http_count("GET", "/api/v1/late-error", status) for status in ("2xx", "5xx")}

    assert http.get("/api/v1/late-error").status_code == 200

    after = {status: http_count("GET", "/api/v1/late-error", status) for status in ("2xx", "5xx")}
    assert after == {"2xx": before["2xx"] + 1, "5xx": before["5xx"]}


def test_unknown_method_does_not_become_label(http: TestClient) -> None:
    before = http_count("other", "/api/v1/items/{item_id}", "4xx")

    assert http.request("BREW", f"/api/v1/items/{uuid4()}").status_code == 405

    assert http_count("other", "/api/v1/items/{item_id}", "4xx") == before + 1


def test_health_and_websocket_are_not_counted(http: TestClient) -> None:
    before = {
        route: sum(
            http_count(method, route, status)
            for method in ("GET", "other")
            for status in ("1xx", "2xx", "3xx", "4xx", "5xx")
        )
        for route in (HEALTH, "/api/v1/ws")
    }

    assert http.get(HEALTH).status_code == 200
    with http.websocket_connect("/api/v1/ws") as websocket:
        assert websocket.receive_text() == "ready"

    after = {
        route: sum(
            http_count(method, route, status)
            for method in ("GET", "other")
            for status in ("1xx", "2xx", "3xx", "4xx", "5xx")
        )
        for route in (HEALTH, "/api/v1/ws")
    }
    assert after == before


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def scrape(port: int) -> str:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=5) as response:
        return str(response.read().decode())


def test_metrics_server_serves_compass_metrics_until_exit() -> None:
    port = free_port()

    with metrics.serve_metrics(port) as served:
        text = scrape(port)

    assert served == port
    assert "# TYPE compass_http_request_duration_seconds histogram" in text
    with pytest.raises(OSError):
        scrape(port)


def test_metrics_server_does_not_start_without_port() -> None:
    with metrics.serve_metrics(None) as served:
        assert served is None


async def test_queue_poll_logs_failed_query_and_keeps_polling() -> None:
    calls: list[str] = []
    stop = asyncio.Event()

    async def count() -> dict[tuple[str, str], int]:
        calls.append("call")
        if len(calls) == 1:
            raise OSError("connection refused")
        stop.set()
        return {("test.poll.recovered", "queued"): 1}

    with capture_logs() as logs:
        await asyncio.wait_for(metrics.poll_queue_jobs(count, stop, interval=0.01), timeout=5)

    assert len(calls) == 2
    assert [entry["event"] for entry in logs] == ["metrics.queue_poll_failed"]
    labels = {"entrypoint": "test.poll.recovered", "status": "queued"}
    assert REGISTRY.get_sample_value("compass_queue_jobs", labels) == 1


@pytest.mark.parametrize("buckets", [metrics.HTTP_BUCKETS, metrics.AI_BUCKETS])
def test_http_and_ai_buckets_reach_model_timeouts(buckets: tuple[float, ...]) -> None:
    # Таймаут LLM — 180 с, разбора PDF — 300 с: долгий ответ не должен уходить в +Inf.
    assert buckets[0] == 0.01
    assert {10.0, 15.0, 20.0, 120.0, 180.0, 300.0} <= set(buckets)
    assert buckets[-1] == 300.0
    assert list(buckets) == sorted(set(buckets))
