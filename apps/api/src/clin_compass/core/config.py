"""Настройки приложения из переменных окружения с префиксом COMPASS_."""

import ipaddress
import os
from pathlib import Path
from typing import Annotated, Literal, Self
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import structlog
from pydantic import (
    Field,
    PositiveFloat,
    PositiveInt,
    SecretStr,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# Адреса узлов одной роли через запятую: http://<tailnet-адрес Мака>:<порт>, без /v1.
NodeUrls = Annotated[tuple[str, ...], NoDecode]
# IP-адреса и сети CIDR через запятую.
Networks = Annotated[tuple[str, ...], NoDecode]
# Минуты шагов эскалации через запятую: повтор врачу, заместитель, администратор.
StepMinutes = Annotated[tuple[int, int, int], NoDecode]

# Переменные, которые больше не читаются, и их замена.
_RETIRED_VARIABLES = {"COMPASS_EMBEDDINGS_BASE_URL": "COMPASS_EMBEDDINGS_URLS"}

log = structlog.get_logger()

# Только для local и test: на стенде секрет обязателен.
_LOCAL_THROTTLE_KEY = b"clin-compass-local-throttle-key"
# 32 символа — openssl rand -hex 16. Короче ключ HMAC перебирается вместе с IP.
_MIN_THROTTLE_SECRET_LENGTH = 32


class Settings(BaseSettings):
    """Неизменяемые настройки. Значения для локальной разработки — в .env.example."""

    # hide_input_in_errors: ошибка конфигурации при старте не повторяет значение переменной.
    # В адресе базы — пароль.
    model_config = SettingsConfigDict(
        env_prefix="COMPASS_", frozen=True, extra="ignore", hide_input_in_errors=True
    )

    database_url: str
    environment: Literal["local", "test", "stand"] = "local"
    session_ttl_hours: int = 12
    invite_ttl_days: int = 7
    # Лимиты частоты (спека #23). Окна заданы в коде: 15 минут для пары «адрес + IP» и для IP,
    # сутки для адреса, час для приглашений и регистраций.
    login_failures_per_pair: PositiveInt = 10
    login_failures_per_ip: PositiveInt = 30
    login_failures_per_account: PositiveInt = 100
    invites_per_doctor: PositiveInt = 30
    registration_failures_per_ip: PositiveInt = 20
    # Секрет HMAC для ключей лимитов. Без него SHA-256 от IPv4 перебирается за минуты.
    # На стенде обязателен; смена секрета обнуляет счётчики.
    throttle_secret: SecretStr | None = None
    # Адреса и сети прокси, чьему X-Forwarded-For верим (на стенде — Caddy во внутренней сети),
    # через запятую. Без них все запросы приходят с адреса Caddy, и лимиты по IP становятся
    # общими. На стенде обязательны; uvicorn запускается с --no-proxy-headers (спека #23, 6).
    trusted_proxies: Networks = ()
    health_timeout_seconds: PositiveFloat = 2.0
    # Узлы инференса по ролям (спека #18, раздел 4). Первый недоступен — клиент берёт следующий.
    # Пустой список — роль не настроена; приложение без неё работает.
    llm_urls: NodeUrls = ()
    embeddings_urls: NodeUrls = ()
    asr_urls: NodeUrls = ()
    docparse_urls: NodeUrls = ()
    rerank_urls: NodeUrls = ()
    # Закрытое S3-хранилище голосовых сообщений. Не задано — текстовый API работает,
    # голосовые операции отвечают 503 до создания клиента SDK.
    s3_endpoint_url: str | None = None
    s3_access_key: str | None = None
    s3_secret_key: SecretStr | None = None
    s3_bucket: str | None = None
    s3_region: str | None = None
    # Извлечение из истории болезни — ~20 с, длинный RAG-контекст — до минуты.
    llm_timeout_seconds: PositiveFloat = 180.0
    # Запрос ~50 мс, батч индексации — секунды; дольше ждать нет смысла, повторит очередь.
    embeddings_timeout_seconds: PositiveFloat = 30.0
    # Запись 10 минут на M3 Max — 13 с; на Маке 18 ГБ вдвое дольше.
    asr_timeout_seconds: PositiveFloat = 120.0
    # Страница PDF в olmOCR 2 — 20–30 с на M3 Max, до 8000 токенов ответа (спека #27, 9.4).
    docparse_timeout_seconds: PositiveFloat = 300.0
    # 20 фрагментов в bge-reranker — медиана 1,2 с, p95 1,5 с на M3 Max (services/rerank).
    # Дольше ждать нет смысла: поиск обойдётся порядком RRF (спека #70, раздел 7).
    rerank_timeout_seconds: PositiveFloat = 10.0
    # После него клиент переходит к следующему узлу.
    ai_connect_timeout_seconds: PositiveFloat = 3.0
    # Каталог knowledge/ с правилами алертов. Пусто — knowledge/ этого репозитория
    # (пакет clin-compass-knowledge установлен из рабочей копии).
    knowledge_dir: Path | None = None
    # Часовой пояс календарных дней расписания шкал (спека #111): день опроса, пропуск.
    timezone: ZoneInfo = ZoneInfo("Europe/Moscow")
    # Шаги эскалации «Обязательно» от момента, когда сигнал стал срочным (спека #56):
    # повтор врачу, заместитель, администраторы. Для демо — 1,2,3.
    escalation_minutes: StepMinutes = (15, 30, 60)
    # API рубрикатора клинических рекомендаций Минздрава: `make index` берёт из него
    # рекомендации манифеста (`?op=GetClinrec2&id=<версия>`, спека #27, 3.2).
    knowledge_guidelines_api_url: str = "https://apicr.minzdrav.gov.ru/api.ashx"
    # Порт HTTP-сервера метрик Prometheus у api и worker (спека #40, 3.4); на стенде — 9100.
    # Не задан — сервер не запускается: локально api и worker на одной машине не делят порт.
    metrics_port: Annotated[int, Field(ge=1, le=65535)] | None = None

    @field_validator(
        "llm_urls",
        "embeddings_urls",
        "asr_urls",
        "docparse_urls",
        "rerank_urls",
        "trusted_proxies",
        mode="before",
    )
    @classmethod
    def _split_list(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        return tuple(part for part in value.split(",") if part.strip())

    @field_validator("llm_urls", "embeddings_urls", "asr_urls", "docparse_urls", "rerank_urls")
    @classmethod
    def _check_urls(cls, urls: tuple[str, ...]) -> tuple[str, ...]:
        roots = tuple(_node_root(url) for url in urls)
        repeated = [root for root in roots if roots.count(root) > 1]
        if repeated:
            raise ValueError(f"адрес узла повторяется в списке: {repeated[0]!r}")
        return roots

    @field_validator("escalation_minutes", mode="before")
    @classmethod
    def _split_minutes(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        return tuple(part.strip() for part in value.split(","))

    @field_validator("escalation_minutes")
    @classmethod
    def _check_minutes(cls, minutes: tuple[int, int, int]) -> tuple[int, int, int]:
        if minutes[0] <= 0 or not minutes[0] < minutes[1] < minutes[2]:
            raise ValueError("escalation steps must be positive and growing")
        return minutes

    @field_validator("trusted_proxies")
    @classmethod
    def _check_proxies(cls, proxies: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_proxy(entry) for entry in proxies)

    @model_validator(mode="after")
    def _report_retired_variables(self) -> Self:
        # Значение не пишем: в старом адресе мог остаться логин или ключ.
        for name, replacement in _RETIRED_VARIABLES.items():
            if name in os.environ:
                log.warning("config.retired_variable", variable=name, replacement=replacement)
        return self

    @model_validator(mode="after")
    def _require_stand_settings(self) -> Self:
        if self.environment != "stand":
            return self
        secret = self.throttle_secret.get_secret_value() if self.throttle_secret else ""
        if len(secret) < _MIN_THROTTLE_SECRET_LENGTH:
            raise ValueError(
                f"на стенде нужен COMPASS_THROTTLE_SECRET не короче"
                f" {_MIN_THROTTLE_SECRET_LENGTH} символов"
            )
        if not self.trusted_proxies:
            raise ValueError("на стенде нужен COMPASS_TRUSTED_PROXIES: адрес или сеть Caddy")
        return self

    @property
    def throttle_key(self) -> bytes:
        """Ключ HMAC для лимитов частоты. Локально и в тестах — постоянный ключ разработки."""
        if self.throttle_secret is None:
            return _LOCAL_THROTTLE_KEY
        return self.throttle_secret.get_secret_value().encode()

    @property
    def cookie_secure(self) -> bool:
        """Флаг Secure у cookie: на стенде только HTTPS, локально и в тестах — HTTP."""
        return self.environment == "stand"


def _proxy(raw: str) -> str:
    """Адрес или сеть прокси в каноническом виде.

    `*` и имена хостов — ошибка при старте: uvicorn принял бы их молча. `*` у него —
    доверять всем, имя хоста он сравнивает как строку с IP клиента.
    """
    entry = raw.strip()
    try:
        if "/" in entry:
            return str(ipaddress.ip_network(entry, strict=False))
        return str(ipaddress.ip_address(entry))
    except ValueError:
        raise ValueError(f"доверенный прокси — IP-адрес или сеть CIDR: {entry!r}") from None


def _node_root(raw: str) -> str:
    """Корень узла без `/` в конце.

    Кривой адрес — ошибка при старте. Иначе health упал бы с 500.
    """
    url = raw.strip().rstrip("/")
    parts = urlsplit(url)
    if "@" in parts.netloc:
        # Адрес попадает в ai_runs.node; сам адрес в ошибку не берём, в нём пароль.
        raise ValueError(f"логин и пароль в адресе узла запрещены: {parts.hostname}")
    try:
        _ = parts.port  # ValueError: порт не число или вне 0–65535
    except ValueError:
        valid = False
    else:
        valid = (
            parts.scheme in ("http", "https")
            and bool(parts.hostname)
            and not any(char.isspace() for char in url)
            and not (parts.path or parts.query or parts.fragment)
        )
    if not valid:
        raise ValueError(f"адрес узла должен быть http(s)://<адрес>:<порт> без пути: {raw!r}")
    return url
