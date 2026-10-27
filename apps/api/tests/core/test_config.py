"""Настройки: списки адресов узлов инференса из переменных окружения (спека #18, раздел 4)."""

import pytest
import structlog
from pydantic import ValidationError

from clin_compass.core.config import Settings

DATABASE_URL = "postgresql+asyncpg://unused/none"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", ()),
        ("http://100.64.0.1:8000", ("http://100.64.0.1:8000",)),
        (
            " http://100.64.0.1:8000/ , https://100.64.0.2:8000 ",
            ("http://100.64.0.1:8000", "https://100.64.0.2:8000"),
        ),
        ("http://100.64.0.1:8000,,", ("http://100.64.0.1:8000",)),
    ],
)
def test_node_urls_are_read_as_comma_separated_list(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: tuple[str, ...]
) -> None:
    monkeypatch.setenv("COMPASS_LLM_URLS", raw)
    monkeypatch.setenv("COMPASS_EMBEDDINGS_URLS", raw)
    monkeypatch.setenv("COMPASS_ASR_URLS", raw)
    monkeypatch.setenv("COMPASS_DOCPARSE_URLS", raw)
    monkeypatch.setenv("COMPASS_RERANK_URLS", raw)

    settings = Settings(database_url=DATABASE_URL)

    assert settings.llm_urls == expected
    assert settings.embeddings_urls == expected
    assert settings.asr_urls == expected
    assert settings.docparse_urls == expected
    assert settings.rerank_urls == expected


@pytest.mark.parametrize(
    "raw",
    [
        "100.64.0.1:8000",
        "ftp://100.64.0.1:8000",
        "http://",
        # Опечатка в порту иначе дошла бы до health и уронила его.
        "http://100.64.0.1:80a",
        "http://100.64.0.1:99999",
        # Адрес — корень узла: /v1 и /health добавляет клиент.
        "http://100.64.0.1:8001/v1",
        "http://100.64.0 .1:8002",
        # Один узел дважды: переход «к следующему адресу» вернул бы запрос на тот же узел.
        "http://100.64.0.1:8002,http://100.64.0.1:8002/",
    ],
)
def test_malformed_node_url_is_rejected(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    monkeypatch.setenv("COMPASS_ASR_URLS", raw)

    with pytest.raises(ValidationError, match="asr_urls"):
        Settings(database_url=DATABASE_URL)


def test_node_urls_passed_directly_are_normalized() -> None:
    settings = Settings(database_url=DATABASE_URL, embeddings_urls=(" http://100.64.0.1:8001/ ",))

    assert settings.embeddings_urls == ("http://100.64.0.1:8001",)


@pytest.mark.parametrize(
    "raw", ["http://compass:s3cret-pass@100.64.0.1:8002", "http://s3cret-pass@100.64.0.1:8002"]
)
def test_node_url_with_credentials_is_rejected_without_echoing_them(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    # Адрес узла пишется в ai_runs.node; пароль не должен попасть ни туда, ни в ошибку.
    monkeypatch.setenv("COMPASS_ASR_URLS", raw)

    with pytest.raises(ValidationError, match="asr_urls") as caught:
        Settings(database_url=DATABASE_URL)

    assert "s3cret-pass" not in str(caught.value)


@pytest.mark.parametrize(
    "field",
    [
        "health_timeout_seconds",
        "llm_timeout_seconds",
        "embeddings_timeout_seconds",
        "asr_timeout_seconds",
        "docparse_timeout_seconds",
        "rerank_timeout_seconds",
        "ai_connect_timeout_seconds",
    ],
)
@pytest.mark.parametrize("value", [0, -1.5])
def test_timeouts_must_be_positive(field: str, value: float) -> None:
    with pytest.raises(ValidationError, match=field):
        Settings.model_validate({"database_url": DATABASE_URL, field: value})


def test_retired_embeddings_base_url_is_reported_by_name_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Переменную заменил COMPASS_EMBEDDINGS_URLS; молча её игнорировать — роль без узлов.
    monkeypatch.setenv("COMPASS_EMBEDDINGS_BASE_URL", "http://100.64.0.9:8001/v1")

    with structlog.testing.capture_logs() as logs:
        Settings(database_url=DATABASE_URL, embeddings_urls=())

    [event] = [entry for entry in logs if entry["event"] == "config.retired_variable"]
    assert event["log_level"] == "warning"
    assert event["variable"] == "COMPASS_EMBEDDINGS_BASE_URL"
    assert event["replacement"] == "COMPASS_EMBEDDINGS_URLS"
    assert "100.64.0.9" not in repr(logs)


def test_without_retired_variable_nothing_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COMPASS_EMBEDDINGS_BASE_URL", raising=False)

    with structlog.testing.capture_logs() as logs:
        Settings(database_url=DATABASE_URL)

    assert logs == []


def test_rate_limits_are_read_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMPASS_LOGIN_FAILURES_PER_PAIR", "3")
    monkeypatch.setenv("COMPASS_LOGIN_FAILURES_PER_IP", "4")
    monkeypatch.setenv("COMPASS_LOGIN_FAILURES_PER_ACCOUNT", "5")
    monkeypatch.setenv("COMPASS_INVITES_PER_DOCTOR", "6")
    monkeypatch.setenv("COMPASS_REGISTRATION_FAILURES_PER_IP", "7")

    settings = Settings(database_url=DATABASE_URL)

    assert (
        settings.login_failures_per_pair,
        settings.login_failures_per_ip,
        settings.login_failures_per_account,
        settings.invites_per_doctor,
        settings.registration_failures_per_ip,
    ) == (3, 4, 5, 6, 7)


RATE_LIMIT_VARIABLES = [
    "COMPASS_LOGIN_FAILURES_PER_PAIR",
    "COMPASS_LOGIN_FAILURES_PER_IP",
    "COMPASS_LOGIN_FAILURES_PER_ACCOUNT",
    "COMPASS_INVITES_PER_DOCTOR",
    "COMPASS_REGISTRATION_FAILURES_PER_IP",
]


def test_rate_limit_defaults_follow_the_design(monkeypatch: pytest.MonkeyPatch) -> None:
    # Спека #23, раздел 2: 10 и 30 за 15 минут, 100 за сутки, 30 и 20 за час.
    # Makefile экспортирует .env разработчика: там пороги могут быть изменены для #41.
    for name in RATE_LIMIT_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    settings = Settings(database_url=DATABASE_URL)

    assert (
        settings.login_failures_per_pair,
        settings.login_failures_per_ip,
        settings.login_failures_per_account,
        settings.invites_per_doctor,
        settings.registration_failures_per_ip,
    ) == (10, 30, 100, 30, 20)


@pytest.mark.parametrize("raw", ["0", "-1"])
def test_rate_limit_must_be_positive(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    monkeypatch.setenv("COMPASS_LOGIN_FAILURES_PER_PAIR", raw)

    with pytest.raises(ValidationError):
        Settings(database_url=DATABASE_URL)


# 32 символа, как у `openssl rand -hex 16`. Без случайных символов: gitleaks принимает
# такую строку в тесте за ключ. Адрес Caddy во внутренней сети стенда.
STAND_SECRET = "s" * 32
STAND_PROXIES = "172.18.0.0/16"


def test_stand_requires_a_throttle_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    # Без секрета хеш ключа лимита — простой SHA-256, и IP из него перебирается (спека #23).
    monkeypatch.delenv("COMPASS_THROTTLE_SECRET", raising=False)
    monkeypatch.setenv("COMPASS_TRUSTED_PROXIES", STAND_PROXIES)

    with pytest.raises(ValidationError, match="COMPASS_THROTTLE_SECRET"):
        Settings(database_url=DATABASE_URL, environment="stand")


@pytest.mark.parametrize("secret", ["", "x" * 31])
def test_stand_rejects_a_short_throttle_secret(
    monkeypatch: pytest.MonkeyPatch, secret: str
) -> None:
    # Пустая строка — не None: без проверки длины HMAC считался бы с пустым ключом.
    monkeypatch.setenv("COMPASS_THROTTLE_SECRET", secret)
    monkeypatch.setenv("COMPASS_TRUSTED_PROXIES", STAND_PROXIES)

    with pytest.raises(ValidationError, match="COMPASS_THROTTLE_SECRET"):
        Settings(database_url=DATABASE_URL, environment="stand")


def test_throttle_key_comes_from_the_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMPASS_THROTTLE_SECRET", STAND_SECRET)
    monkeypatch.setenv("COMPASS_TRUSTED_PROXIES", STAND_PROXIES)

    settings = Settings(database_url=DATABASE_URL, environment="stand")

    assert settings.throttle_key == STAND_SECRET.encode()


def test_stand_requires_trusted_proxies(monkeypatch: pytest.MonkeyPatch) -> None:
    # Без них uvicorn видит адрес Caddy у всех запросов: лимиты по IP становятся общими.
    monkeypatch.setenv("COMPASS_THROTTLE_SECRET", STAND_SECRET)
    monkeypatch.delenv("COMPASS_TRUSTED_PROXIES", raising=False)

    with pytest.raises(ValidationError, match="COMPASS_TRUSTED_PROXIES"):
        Settings(database_url=DATABASE_URL, environment="stand")


def test_trusted_proxies_are_read_as_comma_separated_list(monkeypatch: pytest.MonkeyPatch) -> None:
    # Адрес сети с битами узла приводится к сети: uvicorn такую запись молча не распознал бы.
    monkeypatch.setenv("COMPASS_TRUSTED_PROXIES", " 172.18.0.1/16 , 10.0.0.2,,fd00::/8")

    settings = Settings(database_url=DATABASE_URL)

    assert settings.trusted_proxies == ("172.18.0.0/16", "10.0.0.2", "fd00::/8")


@pytest.mark.parametrize("raw", ["*", "caddy", "172.18.0.0/33", "10.0.0.256"])
def test_malformed_trusted_proxy_is_rejected(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    # `*` у uvicorn — доверять всем: X-Forwarded-For подделал бы любой клиент.
    # Имя хоста uvicorn сравнивает как строку с IP клиента и не находит.
    monkeypatch.setenv("COMPASS_TRUSTED_PROXIES", raw)

    with pytest.raises(ValidationError, match="trusted_proxies"):
        Settings(database_url=DATABASE_URL)


def test_no_trusted_proxies_locally(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COMPASS_TRUSTED_PROXIES", raising=False)

    assert Settings(database_url=DATABASE_URL, environment="local").trusted_proxies == ()


def test_local_runs_without_a_throttle_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COMPASS_THROTTLE_SECRET", raising=False)

    assert Settings(database_url=DATABASE_URL, environment="local").throttle_key


def test_metrics_port_is_read_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMPASS_METRICS_PORT", "9100")

    assert Settings(database_url=DATABASE_URL).metrics_port == 9100


def test_metrics_server_is_off_without_port(monkeypatch: pytest.MonkeyPatch) -> None:
    # Локально api и worker на одной машине не делят порт: без настройки сервера метрик нет.
    monkeypatch.delenv("COMPASS_METRICS_PORT", raising=False)

    assert Settings(database_url=DATABASE_URL).metrics_port is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, (15, 30, 60)),  # спека #56, раздел 2
        ("1,2,3", (1, 2, 3)),  # демо
        (" 5 , 10 , 20 ", (5, 10, 20)),
    ],
)
def test_escalation_minutes(
    monkeypatch: pytest.MonkeyPatch, raw: str | None, expected: tuple[int, int, int]
) -> None:
    if raw is None:
        monkeypatch.delenv("COMPASS_ESCALATION_MINUTES", raising=False)
    else:
        monkeypatch.setenv("COMPASS_ESCALATION_MINUTES", raw)

    assert Settings(database_url=DATABASE_URL).escalation_minutes == expected


@pytest.mark.parametrize("raw", ["15,30", "30,15,60", "0,30,60", "15,15,60"])
def test_escalation_minutes_need_three_growing_steps(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    monkeypatch.setenv("COMPASS_ESCALATION_MINUTES", raw)

    with pytest.raises(ValidationError, match="escalation"):
        Settings(database_url=DATABASE_URL)
