# Образ сервиса ASR (services/asr): FastAPI + sherpa-onnx, GigaAM-v3 e2e_rnnt int8 на CPU.
# Контекст — корень репозитория: docker buildx build --platform linux/amd64 -f services/asr/Dockerfile .
# Модель и Silero VAD скачивает fetch.sh при сборке и сверяет SHA-256 из versions.env:
# контейнеру при старте сеть не нужна. Колёса sherpa-onnx, sherpa-onnx-core, av и numpy
# в uv.lock — manylinux x86_64 и aarch64, нужен glibc.

FROM ghcr.io/astral-sh/uv:0.13.4 AS uv

# Модель: тот же fetch.sh, что на Маке. Ему нужны curl, shasum (пакет perl) и bzip2 для tar -j.
FROM python:3.13.16-slim-trixie AS model
RUN apt-get update \
    && apt-get install -y --no-install-recommends bzip2 curl perl \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /fetch
COPY services/asr/versions.env services/asr/common.sh services/asr/fetch.sh ./
# fetch.sh кладёт модель в $COMPASS_INFERENCE_DIR/models/asr/<MODEL_NAME>. В образ идёт только
# каталог модели, архив остаётся в этой стадии.
RUN COMPASS_INFERENCE_DIR=/cache bash fetch.sh \
    && . ./versions.env \
    && mv "/cache/models/asr/$MODEL_NAME" /model

FROM python:3.13.16-slim-trixie AS build
COPY --from=uv /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0
WORKDIR /app

# uv workspace требует pyproject.toml всех участников, даже если ставит только asr.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=apps/api/pyproject.toml,target=apps/api/pyproject.toml \
    --mount=type=bind,source=knowledge/pyproject.toml,target=knowledge/pyproject.toml \
    --mount=type=bind,source=eval/pyproject.toml,target=eval/pyproject.toml \
    --mount=type=bind,source=services/asr/pyproject.toml,target=services/asr/pyproject.toml \
    uv sync --frozen --no-dev --no-editable --no-install-workspace --package clin-compass-asr

COPY pyproject.toml uv.lock ./
COPY apps/api/pyproject.toml apps/api/
COPY knowledge/pyproject.toml knowledge/
COPY eval/pyproject.toml eval/
COPY services/asr/pyproject.toml services/asr/
COPY services/asr/src services/asr/src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --package clin-compass-asr


FROM python:3.13.16-slim-trixie
LABEL org.opencontainers.image.source="https://github.com/f-normies/shtil" \
      org.opencontainers.image.description="Штиль: ASR, GigaAM-v3 в sherpa-onnx на CPU"

RUN groupadd --system --gid 10001 compass \
    && useradd --system --uid 10001 --gid compass --no-create-home --home-dir /nonexistent compass

COPY --from=build /app/.venv /app/.venv
COPY --from=model /model /opt/asr-model

# ASR_MODEL_DIR и ASR_NUM_THREADS читает Settings (src/clin_compass_asr/settings.py).
# ASR_HOST и ASR_PORT читает только serve.sh, здесь адрес и порт заданы в CMD.
ENV PATH=/app/.venv/bin:$PATH \
    ASR_MODEL_DIR=/opt/asr-model \
    ASR_NUM_THREADS=4 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER compass
EXPOSE 8002

# /health отвечает 200 только после загрузки модели в lifespan. В образе нет curl.
HEALTHCHECK --interval=10s --timeout=5s --start-period=60s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8002/health', timeout=3)"]

# Один процесс: модель в памяти одна, записи ждут очереди внутри него .
CMD ["uvicorn", "--factory", "clin_compass_asr.app:create_app", "--host", "0.0.0.0", "--port", "8002", "--workers", "1"]
