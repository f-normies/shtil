"""Лог неудачной задачи (#170, спека #56, 9.8): ошибка — только после последней попытки.

Без БД: обработчик задачи вызывается напрямую с задачей-заглушкой.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from structlog.testing import capture_logs

from clin_compass.core import queue


def job(attempts: int) -> SimpleNamespace:
    return SimpleNamespace(id=7, attempts=attempts, payload=b"x", created=datetime.now(UTC))


def failures(logs: list[dict[str, object]]) -> list[dict[str, object]]:
    return [e for e in logs if e["event"] == "queue.task_failed"]


async def broken(_: bytes | None) -> None:
    raise RuntimeError("boom")


@pytest.mark.parametrize(("attempts", "last"), [(0, False), (3, False), (4, True), (6, True)])
async def test_failure_is_logged_only_after_the_last_attempt(attempts: int, last: bool) -> None:
    run = queue._job_runner("test.broken", broken, max_attempts=5)
    with capture_logs() as logs, pytest.raises(RuntimeError):
        await run(job(attempts))  # type: ignore[arg-type]

    found = failures(logs)
    assert len(found) == int(last)
    if last:
        assert found[0]["entrypoint"] == "test.broken"
        assert found[0]["log_level"] == "error"


async def test_postponed_task_is_never_a_failure() -> None:
    async def postpone(_: bytes | None) -> None:
        raise queue.RetryRequested(delay=timedelta(seconds=1))

    run = queue._job_runner("test.postpone", postpone, max_attempts=5)
    with capture_logs() as logs, pytest.raises(queue.RetryRequested):
        await run(job(10))  # type: ignore[arg-type]

    assert failures(logs) == []
