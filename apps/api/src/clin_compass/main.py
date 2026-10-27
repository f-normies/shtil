"""Точка входа API. Запуск: `make dev-api`; для Uvicorn нужен лимит `--ws-max-size 4096`."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from clin_compass_knowledge.loader import load_knowledge
from fastapi import FastAPI
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from clin_compass.core import health, realtime
from clin_compass.core.ai.runs import DbRunRecorder
from clin_compass.core.config import Settings
from clin_compass.core.db import make_engine, make_sessionmaker
from clin_compass.core.errors import install_error_handlers
from clin_compass.core.logs import configure_logging
from clin_compass.core.metrics import HttpMetricsMiddleware, serve_metrics
from clin_compass.core.origin import install_origin_guard
from clin_compass.core.storage import build_storage
from clin_compass.modules.adherence import api as adherence_api
from clin_compass.modules.alerts import admin_api as alerts_admin_api
from clin_compass.modules.alerts import api as alerts_api
from clin_compass.modules.alerts import service as alerts_service
from clin_compass.modules.alerts.rules import RuleBook, repository_knowledge_dir
from clin_compass.modules.assessments import api as assessments_api
from clin_compass.modules.assessments.service import ScaleCatalog
from clin_compass.modules.diary import api as diary_api
from clin_compass.modules.diary.errors import DiaryError
from clin_compass.modules.freetext import service as freetext
from clin_compass.modules.identity import api as identity_api
from clin_compass.modules.ingestion import api as ingestion_api
from clin_compass.modules.ingestion import service as ingestion
from clin_compass.modules.knowledge import api as knowledge_api
from clin_compass.modules.knowledge import service as knowledge_service
from clin_compass.modules.messaging import api as messaging_api
from clin_compass.modules.patients import api as patients_api
from clin_compass.modules.proposals import api as proposals_api
from clin_compass.modules.proposals.service import ApplierRegistry
from clin_compass.modules.realtime import api as realtime_api
from clin_compass.modules.summary import api as summary_api
from clin_compass.modules.therapy import api as therapy_api
from clin_compass.modules.therapy import appliers as therapy_appliers
from clin_compass.modules.therapy.checks import Catalog

API_PREFIX = "/api/v1"


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings if settings is not None else Settings()
    configure_logging(resolved.environment)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = make_engine(resolved.database_url)
        app.state.settings = resolved
        app.state.engine = engine
        app.state.sessionmaker = make_sessionmaker(engine)
        app.state.storage = build_storage(resolved)
        # Клиенты моделей для справочных вопросов врача; без узлов эндпоинт отвечает 503.
        app.state.knowledge_ai = knowledge_service.build_ai_if_configured(
            resolved, DbRunRecorder(app.state.sessionmaker)
        )
        listener = realtime.Listener(resolved.database_url, app.state.hub)
        listener.start()
        try:
            # Сервер метрик на своём порту (спека #40, 3.4); без COMPASS_METRICS_PORT его нет.
            with serve_metrics(resolved.metrics_port):
                yield
        finally:
            await listener.stop()
            await engine.dispose()

    app = FastAPI(title="Клинический компас", lifespan=lifespan)
    # Разделы health от модулей: core модули не импортирует (спека #109, 4.3).
    app.state.health_sections = {"knowledge": knowledge_service.index_summary}
    # Модули с клиническими действиями регистрируют здесь свои appliers.
    app.state.proposal_appliers = ApplierRegistry()
    freetext.register_appliers(app.state.proposal_appliers)
    alerts_service.register_appliers(app.state.proposal_appliers)
    app.state.hub = realtime.Hub(delivery_guard=realtime_api.build_delivery_guard(app))
    # Ошибка в данных knowledge/ останавливает старт; шкалы и правила читаются из одного снимка.
    knowledge = load_knowledge(resolved.knowledge_dir or repository_knowledge_dir())
    app.state.alert_rules = RuleBook(
        knowledge.rules.values(),
        knowledge.sources,
        knowledge.signal_decisions,
        knowledge.patient_help,
    )
    app.state.assessment_catalog = ScaleCatalog.from_knowledge(knowledge)
    app.state.drug_catalog = Catalog.from_knowledge(knowledge)
    # Справочник источников для инспектора: манифест, проверенные карточки и снимки ГРЛС.
    # Снимки уже прочитаны справочником препаратов: файл ГРЛС разбирается при старте один раз.
    app.state.knowledge_sources = knowledge_service.SourceCatalog.from_knowledge(
        knowledge, registries=app.state.drug_catalog.registries
    )
    # Назначение при подтверждении сверяется с тем же справочником, что и подбор.
    therapy_appliers.register_appliers(app.state.proposal_appliers, app.state.drug_catalog)
    # Подтверждённый препарат из истории болезни сверяется с тем же справочником.
    ingestion.register_appliers(app.state.proposal_appliers, app.state.drug_catalog)
    install_error_handlers(app)
    app.add_exception_handler(DiaryError, diary_api.handle_error)
    install_origin_guard(app)
    # Адрес клиента — из X-Forwarded-For, только если соединение пришло от доверенного прокси.
    # Добавлен после проверок и потому выполняется раньше них: лимиты и проверки видят адрес
    # клиента.
    # uvicorn запускается с --no-proxy-headers, доверие задаёт только эта настройка (спека #23).
    app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=list(resolved.trusted_proxies))
    # Замер отклика — внешний слой: в него входят все проверки и ответ 500 на необработанную
    # ошибку. Адрес клиента замеру не нужен.
    app.add_middleware(HttpMetricsMiddleware, skip_routes={f"{API_PREFIX}/health"})
    app.include_router(health.router, prefix=API_PREFIX)
    app.include_router(identity_api.router, prefix=API_PREFIX)
    app.include_router(assessments_api.router, prefix=API_PREFIX)
    app.include_router(assessments_api.patient_history_router, prefix=API_PREFIX)
    app.include_router(messaging_api.router, prefix=API_PREFIX)
    app.include_router(alerts_api.router, prefix=API_PREFIX)
    app.include_router(alerts_admin_api.router, prefix=API_PREFIX)
    app.include_router(patients_api.router, prefix=API_PREFIX)
    app.include_router(patients_api.me_router, prefix=API_PREFIX)
    app.include_router(proposals_api.router, prefix=API_PREFIX)
    app.include_router(knowledge_api.router, prefix=API_PREFIX)
    app.include_router(realtime_api.router, prefix=API_PREFIX)
    app.include_router(therapy_api.router, prefix=API_PREFIX)
    app.include_router(diary_api.router, prefix=API_PREFIX)
    app.include_router(ingestion_api.router, prefix=API_PREFIX)
    app.include_router(summary_api.router, prefix=API_PREFIX)
    app.include_router(adherence_api.router, prefix=API_PREFIX)
    # Build the multipart component after all routers are registered; later routers invalidate
    # FastAPI's cached OpenAPI document.
    diary_api.register_voice_upload_openapi_schema(app)
    return app
