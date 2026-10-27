"""Метрики Prometheus для api и worker (спека #40, 3.1).

Метки — только шаблоны маршрутов, имена задач и классы ответов. Id, адресов почты, ФИО
и текста запросов в метках нет. Метрики отдаёт HTTP-сервер `prometheus_client`
на отдельном порту (`COMPASS_METRICS_PORT`). Маршрута `/metrics` в API нет: Caddy не может
отдать метрики наружу даже по ошибке.
"""

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable, Collection, Iterator, Mapping
from typing import Any

import structlog
from prometheus_client import Gauge, Histogram, start_http_server
from starlette.types import ASGIApp, Message, Receive, Scope, Send

log = structlog.get_logger()

# Отклик API и AI-вызов: от быстрых ответов до ответа RAG. Точки 10, 15 и 20 с —
# у порога ответа RAG; 120, 180 и 300 с — у таймаутов моделей (LLM — 180 с, docparse — 300 с).
HTTP_BUCKETS = (
    *(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 15.0, 20.0, 30.0, 60.0),
    *(120.0, 180.0, 300.0),
)
AI_BUCKETS = HTTP_BUCKETS
# Доставка алерта: открытый кабинет получает его за доли секунды, закрытый — дочитывает
# после подключения.
ALERT_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0)
# Задача worker и ожидание в очереди: справка по КР — десятки секунд, разбор PDF — минуты.
TASK_BUCKETS = (*HTTP_BUCKETS[2:], 600.0)
# Как часто worker считает задачи в очереди: так же часто Prometheus опрашивает сервер метрик.
QUEUE_POLL_SECONDS = 15.0

# Путь, для которого не нашлось маршрута.
UNMATCHED = "unmatched"
# Прочие методы сводятся к `other`: имя метода приходит от клиента.
_METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})

HTTP_REQUEST_SECONDS = Histogram(
    "compass_http_request_duration_seconds",
    "Время ответа API по шаблону маршрута",
    ["method", "route", "status"],
    buckets=HTTP_BUCKETS,
)
ALERT_DELIVERY_SECONDS = Histogram(
    "compass_alert_delivery_seconds",
    "От создания алерта до кабинета врача",
    ["origin", "via"],
    buckets=ALERT_BUCKETS,
)
AI_CALL_SECONDS = Histogram(
    "compass_ai_call_duration_seconds",
    "Время AI-вызова: LLM, эмбеддинги, ранжировщик, ASR, разбор PDF",
    ["task", "kind", "status"],
    buckets=AI_BUCKETS,
)
TASK_SECONDS = Histogram(
    "compass_task_duration_seconds",
    "Время задачи worker",
    ["entrypoint", "outcome"],
    buckets=TASK_BUCKETS,
)
TASK_WAIT_SECONDS = Histogram(
    "compass_task_wait_seconds",
    "От постановки задачи до начала первой попытки",
    ["entrypoint"],
    buckets=TASK_BUCKETS,
)
TASK_LATENCY_SECONDS = Histogram(
    "compass_task_latency_seconds",
    "От постановки задачи до успешного завершения, с повторами и паузами между ними",
    ["entrypoint"],
    buckets=TASK_BUCKETS,
)
QUEUE_JOBS = Gauge(
    "compass_queue_jobs",
    "Задачи в очереди PgQueuer по состоянию",
    ["entrypoint", "status"],
)


class HttpMetricsMiddleware:
    """Меряет ответ API: метод, шаблон маршрута и класс ответа (`2xx` … `5xx`).

    Время — до последнего байта тела ответа: фоновые задачи после ответа в него не входят.
    Исключение до конца ответа считается как `5xx`; после отправленного ответа остаётся
    код, который получил клиент. WebSocket и маршруты из `skip_routes` не учитываются:
    сокет живёт минутами, health опрашивает деплой.
    """

    def __init__(self, app: ASGIApp, skip_routes: Collection[str] = ()) -> None:
        self.app = app
        self.skip_routes = frozenset(skip_routes)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = time.perf_counter()
        status = 500
        sent: float | None = None

        async def send_and_watch(message: Message) -> None:
            nonlocal status, sent
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)
            if message["type"] == "http.response.body" and not message.get("more_body"):
                sent = time.perf_counter()

        try:
            await self.app(scope, receive, send_and_watch)
        except Exception:
            if sent is None:
                status = 500
            raise
        finally:
            self._observe(scope, status, (sent or time.perf_counter()) - started)

    def _observe(self, scope: Scope, status: int, seconds: float) -> None:
        route = route_template(scope)
        if route in self.skip_routes:
            return
        method = scope["method"] if scope["method"] in _METHODS else "other"
        HTTP_REQUEST_SECONDS.labels(
            method=method, route=route, status=f"{status // 100}xx"
        ).observe(seconds)


def route_template(scope: Scope) -> str:
    """Шаблон пути совпавшего маршрута с префиксами `include_router`, иначе `unmatched`.

    FastAPI с 0.137 не копирует маршруты при `include_router`, и у `scope["route"].path`
    нет префикса `/api/v1`. Полный шаблон FastAPI кладёт в `scope["fastapi"]`
    (`effective_route_context.path_format`); публичного способа прочитать его нет. Если
    FastAPI это поменяет, останется шаблон без префикса: id в метку всё равно не попадут,
    а тест метрик на настоящем приложении упадёт.
    """
    fastapi_scope: dict[str, Any] = scope.get("fastapi") or {}
    template = getattr(fastapi_scope.get("effective_route_context"), "path_format", None)
    if isinstance(template, str) and template:
        return template
    path = getattr(scope.get("route"), "path", None)
    return path if isinstance(path, str) and path else UNMATCHED


@contextlib.contextmanager
def serve_metrics(port: int | None) -> Iterator[int | None]:
    """HTTP-сервер метрик на `port` до выхода из блока. Без порта сервер не запускается.

    Возвращает порт сервера или None.
    """
    if port is None:
        yield None
        return
    server, thread = start_http_server(port)
    try:
        yield port
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


# Число задач по `entrypoint` и состоянию.
JobCounts = Mapping[tuple[str, str], int]


def set_queue_jobs(counts: JobCounts) -> None:
    """Заменяет `compass_queue_jobs` целиком: сочетания, которых нет в `counts`, удаляются.

    Сначала ставятся новые числа, потом удаляются исчезнувшие: опрос Prometheus между
    шагами не увидит пустую очередь.
    """
    for (entrypoint, status), value in counts.items():
        QUEUE_JOBS.labels(entrypoint=entrypoint, status=status).set(value)
    [family] = QUEUE_JOBS.collect()
    for sample in family.samples:
        key = (sample.labels["entrypoint"], sample.labels["status"])
        if key not in counts:
            QUEUE_JOBS.remove(*key)


async def poll_queue_jobs(
    count: Callable[[], Awaitable[JobCounts]],
    stop: asyncio.Event,
    interval: float = QUEUE_POLL_SECONDS,
) -> None:
    """Раз в `interval` секунд обновляет длину очереди, пока не выставлен `stop`.

    Ошибка запроса пишется в лог, опрос продолжается: worker из-за метрик не падает.
    """
    while not stop.is_set():
        try:
            set_queue_jobs(await count())
        except Exception:
            log.exception("metrics.queue_poll_failed")
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval)
