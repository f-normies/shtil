"""Настройка structlog для api и worker (#41).

Без неё structlog печатает исключение через rich с локальными переменными всех кадров.
Под нагрузкой одна ошибка 500 давала ~15 тыс. строк и секунды процессора в event loop:
api переставал принимать соединения и не оживал после снятия нагрузки. А в лог попадали
данные запроса. Поэтому трейсбек всегда обычный, без локальных переменных; на стенде
запись — одна строка JSON.
"""

from typing import Literal

import structlog
from structlog.typing import Processor

_SHARED: list[Processor] = [
    structlog.contextvars.merge_contextvars,
    structlog.processors.add_log_level,
    structlog.processors.StackInfoRenderer(),
    structlog.dev.set_exc_info,
]


def configure_logging(environment: Literal["local", "test", "stand"]) -> None:
    """Стенд — JSON со временем UTC; локально и в тестах — консоль, как раньше.

    Уже настроенный structlog не трогаем: тесты создают приложение внутри `capture_logs`.
    """
    if structlog.is_configured():
        return
    if environment == "stand":
        renderer: list[Processor] = [
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ]
    else:
        renderer = [
            structlog.processors.TimeStamper(fmt="%Y-%m-%d %H:%M:%S", utc=False),
            structlog.dev.ConsoleRenderer(exception_formatter=structlog.dev.plain_traceback),
        ]
    structlog.configure(processors=[*_SHARED, *renderer])
