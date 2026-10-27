"""Дашборд Grafana опирается на то, что есть в коде (спека #40, раздел 5).

Каждая метрика объявлена в core/metrics.py, каждый `route="…"` — шаблон ручки приложения,
каждый `entrypoint="…"` — задача worker'а. Без этих проверок переименование метрики, ручки
или задачи молча оставит пустую панель на защите.
"""

import json
import re
from pathlib import Path
from uuid import uuid4

import httpx
from prometheus_client.metrics import MetricWrapperBase
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.core import metrics
from clin_compass.core.config import Settings
from clin_compass.worker import build_handlers
from tests.core.test_metrics import http_count

DASHBOARD = (
    Path(__file__).resolve().parents[3] / "infra" / "grafana" / "dashboards" / "compass.json"
)
_METRIC_NAME = re.compile(r"\bcompass_[a-z0-9_]+")
# Значения меток в селекторах `route="…"` и `route!="…"`.
_ROUTE = re.compile(r'\broute!?="([^"]+)"')
_ENTRYPOINT = re.compile(r'\bentrypoint!?="([^"]+)"')
_PATH_PARAM = re.compile(r"\{[^}]+\}")
_STATUSES = ("1xx", "2xx", "3xx", "4xx", "5xx")
# Ряды гистограммы: в запросах стоят они, объявлено базовое имя.
_HISTOGRAM_SUFFIXES = ("_bucket", "_sum", "_count")


def _expressions(node: object) -> list[str]:
    """Все выражения PromQL (`expr`) дашборда, включая панели внутри свёрнутых строк."""
    if isinstance(node, dict):
        found = [node["expr"]] if isinstance(node.get("expr"), str) else []
        return found + [expr for value in node.values() for expr in _expressions(value)]
    if isinstance(node, list):
        return [expr for item in node for expr in _expressions(item)]
    return []


def _base_name(name: str) -> str:
    for suffix in _HISTOGRAM_SUFFIXES:
        if name.endswith(suffix):
            return name.removesuffix(suffix)
    return name


def _dashboard_metrics() -> set[str]:
    board = json.loads(DASHBOARD.read_text(encoding="utf-8"))
    return {_base_name(name) for expr in _expressions(board) for name in _METRIC_NAME.findall(expr)}


def _declared_metrics() -> set[str]:
    return {
        family.name
        for value in vars(metrics).values()
        if isinstance(value, MetricWrapperBase)
        for family in value.describe()
    }


def test_dashboard_queries_compass_metrics() -> None:
    # Иначе следующая проверка прошла бы и на пустом дашборде.
    assert len(_dashboard_metrics()) >= 6


def test_every_dashboard_metric_is_declared() -> None:
    missing = sorted(_dashboard_metrics() - _declared_metrics())

    assert not missing, f"метрики дашборда не объявлены в core/metrics.py: {missing}"


def _label_values(pattern: re.Pattern[str]) -> set[str]:
    board = json.loads(DASHBOARD.read_text(encoding="utf-8"))
    return {value for expr in _expressions(board) for value in pattern.findall(expr)}


def _recorded(route: str) -> float:
    return sum(http_count("GET", route, status) for status in _STATUSES)


async def test_every_dashboard_route_is_template_of_app_route(client: httpx.AsyncClient) -> None:
    # Шаблон берётся так же, как в метрике: запрос идёт через middleware приложения.
    # GET на ручку с другим методом даёт 405, но маршрут совпал, и шаблон записан.
    routes = _label_values(_ROUTE)
    assert routes, "в дашборде нет ни одного route"
    missing = []
    for route in sorted(routes):
        before = _recorded(route)
        await client.get(_PATH_PARAM.sub(str(uuid4()), route))
        if _recorded(route) != before + 1:
            missing.append(route)

    assert not missing, f"ручек дашборда нет в приложении: {missing}"


def test_every_dashboard_entrypoint_is_worker_task(
    settings: Settings, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    # Все узлы заданы: worker регистрирует все задачи. Адреса не опрашиваются.
    configured = settings.model_copy(
        update={
            "llm_urls": ("http://127.0.0.1:8000",),
            "embeddings_urls": ("http://127.0.0.1:8001",),
            "asr_urls": ("http://127.0.0.1:8002",),
            "docparse_urls": ("http://127.0.0.1:8003",),
            "rerank_urls": ("http://127.0.0.1:8004",),
        }
    )
    entrypoints = _label_values(_ENTRYPOINT)
    assert entrypoints, "в дашборде нет ни одного entrypoint"

    missing = sorted(entrypoints - set(build_handlers(configured, sessionmaker)))

    assert not missing, f"задач дашборда нет среди обработчиков worker'а: {missing}"
