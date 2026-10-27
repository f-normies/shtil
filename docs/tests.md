# Тесты

Нужны uv, Node 24, pnpm 10 и Docker: тесты бэкенда сами поднимают Postgres в testcontainers.

```bash
make setup     # зависимости Python и JS
make check     # линтеры, типы и тесты всех частей
```

Одна часть: `make test SCOPE=api` (также `asr`, `knowledge`, `doctor`, `patient`, `ui`, `api-client`). Линтеры и типы — `make lint`, `make typecheck` с тем же `SCOPE`.

Тесты не вызывают настоящие модели. LLM в них подменяется тестовыми моделями PydanticAI, поэтому GPU не нужен.

## Что проверяют

- `apps/api/tests` — сервисы и API всех модулей на настоящем Postgres: права врача и пациента, алерты и эскалация, предложения терапии, дневник, чат, очередь задач. `lint-imports` проверяет границы между модулями.
- `knowledge/tests` — схемы клинических данных, ссылки на источники, пороги шкал. Табличные случаи, у каждого указан источник.
- `services/asr/tests` — нарезка аудио и сервис распознавания.
- `apps/doctor`, `apps/patient`, `packages/ui` — Vitest.

Отдельно проверить данные `knowledge/`: `make knowledge-check`.

## Браузерные тесты

```bash
pnpm --dir apps/patient exec playwright install chromium   # один раз
pnpm --dir apps/patient test:browser              # кабинеты пациента и врача
pnpm --dir apps/patient test:browser:production   # PWA с настоящим service worker
```

## Сквозной тест

Проходит часть сценария: пациент отправляет PHQ-9, врач получает сигнал. Ему нужна отдельная база `compass_e2e` в Postgres с pgvector и свободные порты 8000, 5173 и 5174.

```bash
docker run -d --name shtil-e2e-db -e POSTGRES_USER=compass -e POSTGRES_PASSWORD=compass \
  -e POSTGRES_DB=compass_e2e -p 127.0.0.1:5442:5432 pgvector/pgvector:0.8.7-pg18-trixie
pnpm exec playwright install chromium
COMPASS_DATABASE_URL=postgresql+asyncpg://compass:compass@127.0.0.1:5442/compass_e2e pnpm e2e
```

Тест откатывает и заново накатывает миграции этой базы, грузит сид и запускает API и кабинеты. Отчёт, трасса и логи — в `e2e/artifacts/`.

## Оценка AI

Скрипты в `eval/` гоняют эталонные наборы против работающих моделей. Адрес — сервис модели или api стенда. Наборы и метрики описаны в шапках скриптов.

```bash
# разбор дневника и чата: симптомы, препараты, риск
uv run --package clin-compass-eval python eval/freetext/run.py --base-url http://localhost:8000
# извлечение из историй болезни
uv run --package clin-compass-eval python eval/extraction/run.py --base-url http://localhost:8000
# эмбеддинги: сверка с эталонными векторами
uv run --package clin-compass-eval python eval/embeddings/check.py --base-url http://localhost:8001
# ASR: скорость и память
uv run --package clin-compass-eval python eval/asr/bench.py --base-url http://localhost:8002
```

Порты моделей по умолчанию наружу не публикуются. Для оценки откройте нужный порт в `compose.yaml` (`ports: ["127.0.0.1:8000:8000"]`) или запускайте скрипт в контейнере в сети `shtil_shtil`.

Наши последние результаты лежат рядом с наборами, в `eval/*/results/`.
