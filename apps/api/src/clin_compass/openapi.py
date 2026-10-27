"""Выгрузка OpenAPI-схемы без запуска сервера и без БД: python -m clin_compass.openapi.

Из неё `make gen-client` генерирует типы для фронтендов (packages/api-client).
"""

import json
import sys

import structlog

from clin_compass.core.config import Settings
from clin_compass.main import create_app

# Приложение не стартует (lifespan не вызывается), поэтому адрес БД не используется.
_SCHEMA_ONLY = Settings(database_url="postgresql+asyncpg://schema-only/none")


def main() -> None:
    """Напечатать OpenAPI-схему в stdout."""
    # stdout занят схемой. Логи при сборке приложения (alerts.rules_loaded) идут в stderr.
    structlog.configure(logger_factory=structlog.PrintLoggerFactory(sys.stderr))
    schema = create_app(_SCHEMA_ONLY).openapi()
    json.dump(schema, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
