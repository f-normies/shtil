"""Запись AI-вызовов в ai_runs: каждый вызов, удачный или нет, без текста входа (спека #18)."""

import asyncio
import hashlib
from typing import Any
from uuid import UUID, uuid4

import anyio
import pytest
from prometheus_client import REGISTRY
from pydantic_ai.exceptions import ModelHTTPError
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from clin_compass.core.ai.runs import (
    AiRun,
    DbRunRecorder,
    RunRecord,
    RunSummary,
    hash_input,
    run_summaries,
    track_run,
)
from tests.core.fake_nodes import MemoryRecorder

PROMPT = "Пациент принимает Авиандр 50 мг, жалуется на бессонницу."


async def _rows(sessionmaker: async_sessionmaker[AsyncSession]) -> list[AiRun]:
    async with sessionmaker() as session:
        return list((await session.scalars(select(AiRun))).all())


def test_input_hash_is_sha256_of_utf8_text() -> None:
    assert hash_input(PROMPT) == hashlib.sha256(PROMPT.encode()).hexdigest()
    assert hash_input(b"\x00\x01") == hashlib.sha256(b"\x00\x01").hexdigest()


async def test_successful_call_is_recorded_with_what_client_reported(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    recorder = DbRunRecorder(sessionmaker)

    async with track_run(
        recorder,
        role="llm",
        task="extraction",
        model="qwen3.8-27b",
        input_hash=hash_input(PROMPT),
        params={"temperature": 0.0},
        prompt_version="extraction-v1",
    ) as run:
        run.node = "http://100.64.0.1:8000"
        run.input_tokens = 120
        run.output_tokens = 40

    [row] = await _rows(sessionmaker)
    assert row.id == run.run_id
    assert (row.role, row.task, row.model, row.prompt_version) == (
        "llm",
        "extraction",
        "qwen3.8-27b",
        "extraction-v1",
    )
    assert (row.status, row.error, row.node) == ("ok", None, "http://100.64.0.1:8000")
    assert (row.input_tokens, row.output_tokens, row.audio_seconds) == (120, 40, None)
    assert row.params == {"temperature": 0.0}
    assert row.input_hash == hashlib.sha256(PROMPT.encode()).hexdigest()
    assert row.latency_ms >= 0


async def test_failed_call_is_recorded_and_error_reaches_caller(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    recorder = DbRunRecorder(sessionmaker)

    with pytest.raises(ModelHTTPError):
        async with track_run(
            recorder, role="embeddings", task="knowledge.index", model="giga", input_hash="0" * 64
        ):
            # В теле ответа узел может повторить вход; в ai_runs оно не попадает.
            raise ModelHTTPError(500, "giga", body={"error": PROMPT})

    [row] = await _rows(sessionmaker)
    assert (row.status, row.error, row.node) == ("error", "ModelHTTPError 500", None)
    assert PROMPT not in repr(_columns(row))


class _BrokenRecorder:
    async def record(self, run: RunRecord) -> UUID:
        raise RuntimeError("база недоступна")


async def test_recording_failure_does_not_hide_call_error() -> None:
    with pytest.raises(ModelHTTPError):
        async with track_run(
            _BrokenRecorder(), role="llm", task="extraction", model="q", input_hash="0" * 64
        ):
            raise ModelHTTPError(400, "q")


async def test_recording_failure_after_successful_call_reaches_caller() -> None:
    # Результат без строки в ai_runs не отдаём: на него может сослаться предложение.
    with pytest.raises(RuntimeError, match="база недоступна"):
        async with track_run(
            _BrokenRecorder(), role="llm", task="extraction", model="q", input_hash="0" * 64
        ):
            pass


def _ai_calls(task: str, kind: str, status: str) -> tuple[float, float]:
    """Число и сумма наблюдений `compass_ai_call_duration_seconds` (спека #40, 3.1)."""
    labels = {"task": task, "kind": kind, "status": status}
    name = "compass_ai_call_duration_seconds"
    return (
        REGISTRY.get_sample_value(f"{name}_count", labels) or 0.0,
        REGISTRY.get_sample_value(f"{name}_sum", labels) or 0.0,
    )


async def test_successful_call_is_observed_in_metrics() -> None:
    count, total = _ai_calls("metrics.ok", "rerank", "ok")

    async with track_run(
        MemoryRecorder(), role="rerank", task="metrics.ok", model="bge", input_hash="0" * 64
    ):
        await asyncio.sleep(0.05)

    new_count, new_total = _ai_calls("metrics.ok", "rerank", "ok")
    assert new_count == count + 1
    assert new_total - total >= 0.05


async def test_failed_call_is_observed_in_metrics() -> None:
    before = _ai_calls("metrics.error", "asr", "error")

    with pytest.raises(ModelHTTPError):
        async with track_run(
            MemoryRecorder(), role="asr", task="metrics.error", model="giga", input_hash="0" * 64
        ):
            raise ModelHTTPError(503, "giga")

    assert _ai_calls("metrics.error", "asr", "error")[0] == before[0] + 1


def _columns(row: AiRun) -> dict[str, Any]:
    return {column.key: getattr(row, column.key) for column in AiRun.__table__.columns}


async def test_cancelled_call_is_recorded() -> None:
    # Остановка worker'а или обрыв потокового ответа: вызов был, строка о нём нужна.
    recorder = MemoryRecorder()

    async def call() -> None:
        async with track_run(
            recorder, role="llm", task="extraction", model="q", input_hash="0" * 64
        ):
            await asyncio.sleep(10)

    task = asyncio.create_task(call())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    [run] = recorder.runs
    assert (run.status, run.error) == ("error", "CancelledError")


class _SlowRecorder(MemoryRecorder):
    """Запись ждёт ввода-вывода, как поход в базу."""

    async def record(self, run: RunRecord) -> UUID:
        await asyncio.sleep(0.01)
        return await super().record(run)


async def test_call_cancelled_by_anyio_scope_is_recorded() -> None:
    # Отмена anyio (таймаут Starlette, отмена группы задач) повторяется на каждом await
    # внутри отменённой области: без защиты запись тоже отменилась бы.
    recorder = _SlowRecorder()

    with anyio.move_on_after(0.05):
        async with track_run(
            recorder, role="llm", task="extraction", model="q", input_hash="0" * 64
        ):
            await asyncio.sleep(10)

    [run] = recorder.runs
    assert (run.status, run.error) == ("error", "CancelledError")


async def test_successful_call_is_recorded_when_scope_is_cancelled_during_recording() -> None:
    # Вызов прошёл, срок области истёк во время записи: строка всё равно нужна.
    recorder = _SlowRecorder()

    with anyio.move_on_after(0.005) as scope:
        async with track_run(
            recorder, role="llm", task="extraction", model="q", input_hash="0" * 64
        ) as run:
            pass

    assert scope.cancel_called
    [record] = recorder.runs
    assert record.status == "ok"
    assert run.run_id is not None


async def test_db_recorder_returns_id_with_default_session_settings(postgres_url: str) -> None:
    # Фабрика сессий вызывающего кода может истекать объекты при коммите (так по умолчанию).
    engine = create_async_engine(postgres_url)
    recorder = DbRunRecorder(async_sessionmaker(engine))

    async with track_run(
        recorder, role="asr", task="diary.transcribe", model="g", input_hash="0" * 64
    ) as run:
        run.audio_seconds = 8.61
    await engine.dispose()

    assert isinstance(run.run_id, UUID)


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE ai.ai_runs SET status = 'error'",
        "DELETE FROM ai.ai_runs",
        "TRUNCATE ai.ai_runs",
    ],
)
async def test_ai_runs_is_append_only(
    sessionmaker: async_sessionmaker[AsyncSession], statement: str
) -> None:
    # На строку ai_runs ссылается предложение (origin_ref): журнал только дописывается.
    async with track_run(
        DbRunRecorder(sessionmaker), role="llm", task="extraction", model="q", input_hash="0" * 64
    ):
        pass

    async with sessionmaker() as session:
        with pytest.raises(DBAPIError, match="append-only"):
            await session.execute(text(statement))


async def test_run_summaries_reads_model_prompt_and_start_of_known_runs(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    # Очередь предложений показывает врачу версию правил разбора (спека #96, раздел 3.2).
    recorder = DbRunRecorder(sessionmaker)
    async with track_run(
        recorder,
        role="llm",
        task="freetext.extract",
        model="qwen3.6",
        input_hash="0" * 64,
        prompt_version="extract-7a3603ce",
    ) as with_prompt:
        pass
    async with track_run(
        recorder, role="asr", task="diary.transcribe", model="gigaam", input_hash="0" * 64
    ) as without_prompt:
        pass
    assert with_prompt.run_id is not None
    assert without_prompt.run_id is not None
    rows = {row.id: row for row in await _rows(sessionmaker)}
    missing = uuid4()

    async with sessionmaker() as session:
        found = await run_summaries(session, [with_prompt.run_id, without_prompt.run_id, missing])

    assert found == {
        with_prompt.run_id: RunSummary(
            model="qwen3.6",
            prompt_version="extract-7a3603ce",
            created_at=rows[with_prompt.run_id].created_at,
        ),
        without_prompt.run_id: RunSummary(
            model="gigaam", prompt_version=None, created_at=rows[without_prompt.run_id].created_at
        ),
    }


async def test_run_summaries_of_no_ids_is_empty(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with sessionmaker() as session:
        assert await run_summaries(session, []) == {}
