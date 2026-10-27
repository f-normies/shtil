"""Логи api и worker (#41): ошибка не печатается через rich и не выносит локальные переменные.

Без настройки structlog рисует исключение таблицами rich с локальными переменными всех
кадров: под нагрузкой одна ошибка 500 давала ~15 тыс. строк и секунды процессора в event
loop, а в лог попадали данные запроса.
"""

import json
from collections.abc import Iterator

import pytest
import structlog

from clin_compass.core.config import Settings
from clin_compass.core.logs import configure_logging
from clin_compass.main import create_app

SECRET = "данные-пациента-в-локальной-переменной"


@pytest.fixture(autouse=True)
def _reset_structlog() -> Iterator[None]:
    # До и после: create_app в других тестах уже мог настроить structlog.
    structlog.reset_defaults()
    yield
    structlog.reset_defaults()


def _log_failure() -> None:
    patient_note = SECRET  # noqa: F841 - локальная переменная кадра, которую лог не должен показать
    try:
        raise TimeoutError("QueuePool limit reached")
    except TimeoutError as error:
        structlog.get_logger().error("http.unhandled_error", path="/api/v1/x", exc_info=error)


def test_stand_writes_one_json_line_with_plain_traceback(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging("stand")

    _log_failure()

    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["event"] == "http.unhandled_error"
    assert entry["level"] == "error"
    assert entry["exception"].startswith("Traceback (most recent call last):")
    assert "TimeoutError: QueuePool limit reached" in entry["exception"]
    assert SECRET not in lines[0]


def test_local_console_keeps_traceback_without_locals(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("local")

    _log_failure()

    out = capsys.readouterr().out
    assert "TimeoutError: QueuePool limit reached" in out
    assert SECRET not in out
    assert "╭" not in out


def test_create_app_configures_stand_logging(capsys: pytest.CaptureFixture[str]) -> None:
    create_app(
        Settings(
            database_url="postgresql+asyncpg://compass@127.0.0.1:1/compass",
            environment="stand",
            throttle_secret="0" * 32,
            trusted_proxies=("10.88.0.10",),
        )
    )

    _log_failure()

    entry = json.loads(capsys.readouterr().out)
    assert entry["event"] == "http.unhandled_error"


def test_existing_configuration_is_kept() -> None:
    """Тесты создают приложение внутри capture_logs: перехват не должен пропасть."""
    with structlog.testing.capture_logs() as logs:
        configure_logging("stand")
        structlog.get_logger().warning("health.section_failed", section="knowledge")

    assert [entry["event"] for entry in logs] == ["health.section_failed"]
