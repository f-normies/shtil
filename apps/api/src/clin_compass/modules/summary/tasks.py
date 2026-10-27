"""Задача worker'а: черновик сводки по ключу данных (спека #38, 3.3).

Аргумент задачи — ключ данных строки `pending`. Факты берутся из снимка строки. Вызов модели
идёт вне транзакции, ответ пишется одной транзакцией.

Ключ устарел (данные пациента уже другие) — модель не вызывается, строка остаётся `pending`.
Её черновик никто не увидел бы. Если данные вернутся к этому ключу, `GET` найдёт строку
`pending` и поставит задачу снова (`service._request`): строка не застревает.

Повторы задача делает сама через `RetryRequested`, как справка по КР (#107): пока узлы LLM
недоступны, она ждёт без счёта попыток. Другие ошибки считаются в строке; после
`FAILURE_LIMIT` неудач строка получает `failed`, и врач видит «Повторить». Ответ, в котором
проверка отбросила все утверждения, сразу даёт `failed`: на тех же фактах модель ответит так же.
"""

from dataclasses import dataclass
from datetime import timedelta
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.core.ai.llm import Llm, build_llm_model
from clin_compass.core.ai.nodes import NodesUnavailableError
from clin_compass.core.ai.runs import DbRunRecorder, describe_error
from clin_compass.core.config import Settings
from clin_compass.core.queue import Entrypoint, RetryRequested
from clin_compass.modules.summary import service
from clin_compass.modules.summary.drafting import Drafter, StatementsRejectedError
from clin_compass.modules.summary.models import VisitSummary
from clin_compass.modules.summary.schemas import SummaryFact
from clin_compass.modules.summary.service import ENTRYPOINT

log = structlog.get_logger()

# После третьей неудачи строка получает `failed`, врач видит «Повторить».
FAILURE_LIMIT = 3
# Пока Мак выключен, задача проверяет узел раз в 30 с.
UNAVAILABLE_DELAY = timedelta(seconds=30)
_FIRST_RETRY_SECONDS = 5
# Во всех процессах worker'а — не больше одной сводки за раз: узел LLM нужен и freetext.
_CONCURRENCY = 1


@dataclass(frozen=True)
class Jobs:
    """Обработчик черновика сводки с агентом модели."""

    sessionmaker: async_sessionmaker[AsyncSession]
    drafter: Drafter
    # Часовой пояс дат в фактах: по нему задача сверяет свой ключ с текущим.
    zone: ZoneInfo

    def handlers(self) -> dict[str, Entrypoint]:
        """Точки входа для `worker.build_handlers`."""
        return {ENTRYPOINT: Entrypoint(self.draft, concurrency_limit=_CONCURRENCY)}

    async def draft(self, payload: bytes | None) -> None:
        """Черновик по ключу данных; наружу выходит только `RetryRequested`.

        Обычная ошибка из обработчика могла бы стать последней попыткой PgQueuer без нашей
        отметки `failed` (`core/queue`). Повтор поднимается без цепочки причин: в ошибке
        бывают данные пациента. В логах — только ключ и класс ошибки.
        """
        key = _key(payload)
        try:
            await self._compute(key)
        except NodesUnavailableError:
            raise RetryRequested(delay=UNAVAILABLE_DELAY, reason="llm nodes unavailable") from None
        except Exception as exc:  # noqa: BLE001 — любая ошибка: повтор или отметка о неудаче
            await self._failed(key, exc)

    async def _failed(self, key: str, exc: Exception) -> None:
        """Неудача в строке; повтор — `RetryRequested`, последняя неудача — `failed`."""
        reason = describe_error(exc)
        final = isinstance(exc, StatementsRejectedError)
        try:
            failures = await self._record_failure(key, final=final)
        except Exception as record_exc:  # noqa: BLE001 — см. `draft`
            # База worker'а недоступна: ждём её так же, как узел модели.
            log.warning(
                "summary.failure_not_recorded",
                data_key=key,
                error=reason,
                record_error=describe_error(record_exc),
            )
            raise RetryRequested(delay=UNAVAILABLE_DELAY, reason=reason) from None
        log.warning("summary.draft_error", data_key=key, error=reason, failures=failures)
        if failures is None:
            # Строка уже посчитана или упала: ни считать, ни отмечать нечего.
            return
        if not final and failures < FAILURE_LIMIT:
            delay = timedelta(seconds=_FIRST_RETRY_SECONDS * 2 ** (failures - 1))
            raise RetryRequested(delay=delay, reason=reason) from None
        log.error("summary.draft_failed", data_key=key, failures=failures)

    async def _compute(self, key: str) -> None:
        async with self.sessionmaker() as session:
            row = await session.scalar(
                select(VisitSummary).where(
                    VisitSummary.data_key == key, VisitSummary.status == "pending"
                )
            )
            if row is None:
                return
            facts = [SummaryFact.model_validate(f) for f in row.facts]
            current = await service.current_key(session, row.patient_id, self.zone)
        if current != key:
            log.info("summary.draft_superseded", data_key=key)
            return
        draft = await self.drafter.draft(facts)
        async with self.sessionmaker() as session:
            await session.execute(
                update(VisitSummary)
                .where(VisitSummary.data_key == key, VisitSummary.status == "pending")
                .values(
                    status="done",
                    draft=[s.model_dump(mode="json") for s in draft.statements],
                    dropped=draft.dropped,
                    run_id=draft.run_id,
                    computed_at=func.now(),
                )
            )
            await session.commit()

    async def _record_failure(self, key: str, *, final: bool) -> int | None:
        """Счётчик неудач строки `pending` +1; на `FAILURE_LIMIT` или с `final` — `failed`.

        Нет строки `pending` — None.
        """
        async with self.sessionmaker() as session:
            count: int | None = await session.scalar(
                update(VisitSummary)
                .where(VisitSummary.data_key == key, VisitSummary.status == "pending")
                .values(failures=VisitSummary.failures + 1)
                .returning(VisitSummary.failures)
            )
            if count is not None and (final or count >= FAILURE_LIMIT):
                await session.execute(
                    update(VisitSummary)
                    .where(VisitSummary.data_key == key, VisitSummary.status == "pending")
                    .values(status="failed")
                )
            await session.commit()
        return count


def build_jobs(settings: Settings, sessionmaker: async_sessionmaker[AsyncSession]) -> Jobs:
    """Задача с моделью из узлов `COMPASS_LLM_URLS`; вызовы пишутся в ai_runs.

    Raises:
        AiNotConfiguredError: Адреса узлов LLM не заданы.
    """
    llm = Llm(build_llm_model(settings), DbRunRecorder(sessionmaker))
    return Jobs(sessionmaker, Drafter(llm, settings.timezone), settings.timezone)


def _key(payload: bytes | None) -> str:
    if payload is None:
        raise ValueError("summary draft job without data key")
    return payload.decode()
