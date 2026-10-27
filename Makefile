# «Штиль» — единые команды. `make help` показывает все цели.
# Требуется GNU make 3.81+ (стоит на macOS), uv, pnpm, Docker.

SHELL := /bin/sh
.DEFAULT_GOAL := help

# Переменные из .env доступны всем командам (uv, alembic, docker compose).
-include .env
export

API := uv run --package clin-compass-api --directory apps/api
ASR := uv run --package clin-compass-asr --directory services/asr
KNOWLEDGE := uv run --package clin-compass-knowledge --directory knowledge

# SCOPE ограничивает lint/typecheck/test одной частью монорепо: SCOPE=api|asr|knowledge|doctor|patient|ui|api-client.
SCOPE ?= all
WEB_SCOPES := doctor patient ui api-client
ifeq ($(SCOPE),all)
  SCOPES := api asr knowledge $(WEB_SCOPES)
else
  SCOPES := $(SCOPE)
endif

# Каталоги TS-частей для шаблонных целей lint-%, typecheck-%, test-%.
DIR_doctor := apps/doctor
DIR_patient := apps/patient
DIR_ui := packages/ui
DIR_api-client := packages/api-client

.PHONY: help
help: ## Список целей
	@grep -hE '^[a-zA-Z0-9_-]+:.*## ' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

# --- Окружение -------------------------------------------------------------

.PHONY: setup
setup: .env ## Зависимости для разработки и тестов (первый запуск)
	uv sync --all-packages
	pnpm install

.env:
	cp .env.example .env
	@echo "Создан .env — заполните строки с <...> (docs/deploy.md)"

.PHONY: gen-client
gen-client: ## OpenAPI бэкенда → типы packages/api-client
	$(API) python -m clin_compass.openapi > packages/api-client/openapi.json
	pnpm --dir packages/api-client gen

# --- Локальный стенд (compose.yaml) ------------------------------------------

STAND := docker compose --env-file .env

.PHONY: stand-up stand-up-gpu stand-down stand-seed stand-ps stand-logs
stand-up: .env ## Стенд без моделей: http://localhost:8080
	$(STAND) up -d --build --wait

stand-up-gpu: .env ## Стенд вместе с моделями на NVIDIA GPU
	$(STAND) --profile gpu up -d --build --wait

stand-down: ## Остановить стенд (данные в томах остаются)
	$(STAND) --profile gpu down

stand-seed: ## Демо-данные: врачи, пациенты, история шкал
	$(STAND) exec api python -m clin_compass.cli seed

stand-ps: ## Статус контейнеров стенда
	$(STAND) --profile gpu ps

stand-logs: ## Логи стенда
	$(STAND) --profile gpu logs -f

# --- Проверки --------------------------------------------------------------

.PHONY: lint typecheck test check fmt
lint: $(addprefix lint-,$(SCOPES)) ## Линтинг (SCOPE=…)
typecheck: $(addprefix typecheck-,$(SCOPES)) ## Проверка типов (SCOPE=…)
test: $(addprefix test-,$(SCOPES)) ## Тесты (SCOPE=…)
check: lint typecheck test ## Линтеры, типы и тесты

fmt: ## Автоформат Python и TS
	$(API) ruff check --fix .
	$(API) ruff format .
	$(ASR) ruff check --fix .
	$(ASR) ruff format .
	$(KNOWLEDGE) ruff check --fix .
	$(KNOWLEDGE) ruff format .
	pnpm exec biome check --write .

.PHONY: lint-api typecheck-api test-api
lint-api:
	$(API) ruff check .
	$(API) ruff format --check .
	$(API) lint-imports

typecheck-api:
	$(API) mypy

test-api:
	$(API) pytest

.PHONY: lint-asr typecheck-asr test-asr
lint-asr:
	$(ASR) ruff check .
	$(ASR) ruff format --check .

typecheck-asr:
	$(ASR) mypy

test-asr:
	$(ASR) pytest

# Пакет схем knowledge/: тесты прогоняют и проверку настоящих данных (спека knowledge-skeleton).
.PHONY: lint-knowledge typecheck-knowledge test-knowledge knowledge-check
lint-knowledge:
	$(KNOWLEDGE) ruff check .
	$(KNOWLEDGE) ruff format --check .

typecheck-knowledge:
	$(KNOWLEDGE) mypy

test-knowledge:
	$(KNOWLEDGE) pytest

knowledge-check: ## Проверить данные knowledge/: схемы, ссылки на источники, пороги
	$(KNOWLEDGE) python -m clin_compass_knowledge validate

lint-%:
	pnpm exec biome check $(DIR_$*)

typecheck-%:
	pnpm --dir $(DIR_$*) typecheck

test-%:
	pnpm --dir $(DIR_$*) run --if-present test
