"""Журнал AI-вызовов `ai.ai_runs`: одна строка на вызов клиента core/ai (спека #18, раздел 5).

Строка пишется в своей короткой транзакции, поэтому остаётся, даже если бизнес-транзакция
откатилась. Текст входа и выхода не пишется никогда — это медицинские данные; вход — хеш SHA-256.
"""

import hashlib
import time
from collections.abc import AsyncIterator, Collection
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, Protocol
from uuid import UUID, uuid4

import anyio
import httpx2
import structlog
from pydantic_ai.exceptions import ModelHTTPError
from sqlalchemy import CheckConstraint, DateTime, Float, Integer, String, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from clin_compass.core import metrics
from clin_compass.core.ai.nodes import AiRole
from clin_compass.core.orm import Base, uuid_pk

SCHEMA = "ai"

log = structlog.get_logger()


class AiRun(Base):
    """Один вызов модели: кто, какой моделью, на каком узле, сколько длился, чем кончился."""

    __tablename__ = "ai_runs"
    __table_args__ = (
        CheckConstraint("status IN ('ok', 'error')", name="status"),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = uuid_pk()
    # Начало вызова; строка пишется после его окончания.
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    role: Mapped[str] = mapped_column(String(16))
    task: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(100))
    # Узел, который ответил; нет ответа — null.
    node: Mapped[str | None] = mapped_column(String(200))
    prompt_version: Mapped[str | None] = mapped_column(String(32))
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    input_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(8))
    # Класс ошибки и код HTTP. Тело ответа не пишется: в нём узел может повторить вход.
    error: Mapped[str | None] = mapped_column(String(200))
    latency_ms: Mapped[int] = mapped_column(Integer)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    # Длительность записи ASR: RTF = latency_ms / 1000 / audio_seconds.
    audio_seconds: Mapped[float | None] = mapped_column(Float)


@dataclass(frozen=True)
class RunRecord:
    """Готовая к записи строка ai_runs."""

    started_at: datetime
    role: AiRole
    task: str
    model: str
    node: str | None
    prompt_version: str | None
    params: dict[str, Any]
    input_hash: str
    status: Literal["ok", "error"]
    error: str | None
    latency_ms: int
    input_tokens: int | None
    output_tokens: int | None
    audio_seconds: float | None


class RunRecorder(Protocol):
    """Куда пишутся AI-вызовы. В приложении — `DbRunRecorder`."""

    async def record(self, run: RunRecord) -> UUID:
        """Пишет вызов и возвращает id его строки ai_runs."""
        ...


class DbRunRecorder:
    """Пишет строку ai_runs в своей транзакции."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def record(self, run: RunRecord) -> UUID:
        """Добавляет строку ai_runs и коммитит её отдельно от бизнес-транзакции."""
        row = AiRun(
            created_at=run.started_at,
            role=run.role,
            task=run.task,
            model=run.model,
            node=run.node,
            prompt_version=run.prompt_version,
            params=run.params,
            input_hash=run.input_hash,
            status=run.status,
            error=run.error,
            latency_ms=run.latency_ms,
            input_tokens=run.input_tokens,
            output_tokens=run.output_tokens,
            audio_seconds=run.audio_seconds,
        )
        async with self._sessionmaker() as session:
            session.add(row)
            await session.flush()
            # id читаем до коммита: фабрика сессий может истекать объекты при коммите.
            run_id = row.id
            await session.commit()
        return run_id


class NullRecorder:
    """Ничего не пишет. Только для eval/, где нет базы; приложение его не использует."""

    async def record(self, run: RunRecord) -> UUID:
        """Ничего не пишет и возвращает новый id: ссылаться на него нечему."""
        return uuid4()


@dataclass
class RunTracker:
    """Вызов в процессе. Клиент дописывает то, что узнал из ответа."""

    model: str
    node: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    audio_seconds: float | None = None
    # Появляется после выхода из track_run.
    run_id: UUID | None = None


@dataclass(frozen=True)
class AiResult[T]:
    """Ответ модели и id строки ai_runs: на него ссылается предложение (`origin_ref`)."""

    output: T
    run_id: UUID


@asynccontextmanager
async def track_run(
    recorder: RunRecorder,
    *,
    role: AiRole,
    task: str,
    model: str,
    input_hash: str,
    params: dict[str, Any] | None = None,
    prompt_version: str | None = None,
) -> AsyncIterator[RunTracker]:
    """Меряет вызов и при выходе пишет его в ai_runs: `ok` или `error` с классом ошибки.

    Длительность и исход идут и в метрику `compass_ai_call_duration_seconds` (спека #40).

    Ошибка вызова уходит наружу, отмена тоже пишется (`CancelledError`). Если при этом
    не удалась и запись, ошибка записи только логируется. Если вызов прошёл, а запись нет,
    наружу уходит ошибка записи: результат без строки в ai_runs не отдаём.
    """
    tracker = RunTracker(model=model)
    started_at = datetime.now(UTC)
    started = time.perf_counter()

    def finished(status: Literal["ok", "error"], error: str | None) -> RunRecord:
        seconds = time.perf_counter() - started
        metrics.AI_CALL_SECONDS.labels(task=task, kind=role, status=status).observe(seconds)
        return RunRecord(
            started_at=started_at,
            role=role,
            task=task,
            model=tracker.model,
            node=tracker.node,
            prompt_version=prompt_version,
            params=params or {},
            input_hash=input_hash,
            status=status,
            error=error,
            latency_ms=round(seconds * 1000),
            input_tokens=tracker.input_tokens,
            output_tokens=tracker.output_tokens,
            audio_seconds=tracker.audio_seconds,
        )

    # Запись защищена от отмены (`CancelScope(shield=True)`). Отмена anyio (таймаут Starlette,
    # отмена группы задач) повторяется на каждом await внутри отменённой области, и без защиты
    # строка о вызове не записалась бы.
    try:
        yield tracker
    except BaseException as exc:
        # BaseException: отменённый вызов (остановка worker'а, обрыв потока) тоже был.
        with anyio.CancelScope(shield=True):
            try:
                tracker.run_id = await recorder.record(finished("error", describe_error(exc)))
            except Exception:
                # Любая ошибка записи не должна скрыть ошибку вызова.
                log.exception("ai_runs.record_failed", role=role, task=task)
        raise
    with anyio.CancelScope(shield=True):
        tracker.run_id = await recorder.record(finished("ok", None))


@dataclass(frozen=True)
class RunSummary:
    """Что показать врачу о вызове: модель, версия промпта и начало вызова."""

    model: str
    prompt_version: str | None
    created_at: datetime


async def run_summaries(session: AsyncSession, run_ids: Collection[UUID]) -> dict[UUID, RunSummary]:
    """Сводки вызовов по id одним запросом. Id без строки в ai_runs в ответ не попадают."""
    if not run_ids:
        return {}
    rows = await session.execute(
        select(AiRun.id, AiRun.model, AiRun.prompt_version, AiRun.created_at).where(
            AiRun.id.in_(run_ids)
        )
    )
    return {
        row.id: RunSummary(
            model=row.model, prompt_version=row.prompt_version, created_at=row.created_at
        )
        for row in rows
    }


def hash_input(data: str | bytes) -> str:
    """SHA-256 входа: строки — в UTF-8."""
    raw = data.encode() if isinstance(data, str) else data
    return hashlib.sha256(raw).hexdigest()


def describe_error(exc: BaseException) -> str:
    """Класс ошибки и код HTTP, если он есть. Текст ошибки не берём: в нём может быть вход."""
    name = type(exc).__name__
    status: object = getattr(exc, "status", None)
    if isinstance(exc, ModelHTTPError):
        status = exc.status_code
    elif isinstance(exc, httpx2.HTTPStatusError):
        status = exc.response.status_code
    return f"{name} {status}"[:200] if isinstance(status, int) else name[:200]


__all__ = [
    "AiResult",
    "AiRun",
    "DbRunRecorder",
    "NullRecorder",
    "RunRecord",
    "RunRecorder",
    "RunSummary",
    "RunTracker",
    "describe_error",
    "hash_input",
    "run_summaries",
    "track_run",
]
