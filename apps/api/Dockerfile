# Образ api (спека #15, 4.1). Из него запускаются api, worker и migrate.
# Контекст — корень репозитория: docker buildx build -f apps/api/Dockerfile .
# Нужен glibc: колёса asyncpg, pypdfium2, pillow, argon2-cffi и uvloop в uv.lock — manylinux.

FROM ghcr.io/astral-sh/uv:0.13.4 AS uv

FROM python:3.13.16-slim-trixie AS build
COPY --from=uv /uv /bin/uv
# Байт-код собирается при сборке: в работающем контейнере каталог .venv только для чтения.
# Python берётся из базового образа, тот же путь будет в итоговом.
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0
WORKDIR /app

# Сначала только зависимости: этот слой меняется реже кода.
# --frozen: строго по uv.lock; --no-dev: без линтеров и тестов;
# --no-editable: пакеты копируются в .venv; --package: api и knowledge без eval и asr.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=apps/api/pyproject.toml,target=apps/api/pyproject.toml \
    --mount=type=bind,source=knowledge/pyproject.toml,target=knowledge/pyproject.toml \
    --mount=type=bind,source=eval/pyproject.toml,target=eval/pyproject.toml \
    --mount=type=bind,source=services/asr/pyproject.toml,target=services/asr/pyproject.toml \
    uv sync --frozen --no-dev --no-editable --no-install-workspace --package clin-compass-api

COPY pyproject.toml uv.lock ./
COPY apps/api/pyproject.toml apps/api/
COPY apps/api/src apps/api/src
COPY knowledge/pyproject.toml knowledge/
COPY knowledge/src knowledge/src
COPY eval/pyproject.toml eval/
COPY services/asr/pyproject.toml services/asr/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --package clin-compass-api


FROM python:3.13.16-slim-trixie
LABEL org.opencontainers.image.source="https://github.com/f-normies/shtil" \
      org.opencontainers.image.description="Клинический компас: api, worker и migrate"

# Пользователь без root: уязвимость в зависимости не даёт root в контейнере.
RUN groupadd --system --gid 10001 compass \
    && useradd --system --uid 10001 --gid compass --no-create-home --home-dir /nonexistent compass

COPY --from=build /app/.venv /app/.venv
COPY apps/api/alembic.ini /app/apps/api/alembic.ini
COPY apps/api/migrations /app/apps/api/migrations
# Данные knowledge/ без тестов и исходников пакета: api и worker не стартуют
# без файлов из манифеста (sources/files, в том числе grls.csv).
COPY knowledge/cases /app/knowledge/cases
COPY knowledge/drug-groups /app/knowledge/drug-groups
COPY knowledge/drugs /app/knowledge/drugs
COPY knowledge/rules /app/knowledge/rules
COPY knowledge/scales /app/knowledge/scales
COPY knowledge/scenarios /app/knowledge/scenarios
COPY knowledge/signs /app/knowledge/signs
COPY knowledge/sources /app/knowledge/sources
COPY knowledge/signal-decisions.yaml /app/knowledge/signal-decisions.yaml
COPY knowledge/scale-schedule.yaml /app/knowledge/scale-schedule.yaml
COPY knowledge/patient-help.yaml /app/knowledge/patient-help.yaml

# Путь к knowledge/ по умолчанию считается от пакета, а при --no-editable пакет лежит
# в site-packages. Поэтому каталог задан явно.
ENV PATH=/app/.venv/bin:$PATH \
    COMPASS_KNOWLEDGE_DIR=/app/knowledge \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Здесь alembic.ini: migrate запускается командой `alembic upgrade head`.
WORKDIR /app/apps/api
USER compass
EXPOSE 8000

# --factory: приложение создаёт create_app(). --no-proxy-headers: X-Forwarded-For разбирает
# приложение по COMPASS_TRUSTED_PROXIES. --ws-max-size 4096: кадры WebSocket больше 4 КиБ
# отклоняет транспорт. worker — `python -m clin_compass.worker`.
CMD ["uvicorn", "--factory", "clin_compass.main:create_app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers", "--ws-max-size", "4096"]
