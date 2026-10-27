"""Задача очереди модуля alerts: шаги эскалации «Обязательно» (спека #56, `chain.py`)."""

from dataclasses import dataclass
from datetime import UTC, datetime

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.core.config import Settings
from clin_compass.core.queue import Entrypoint
from clin_compass.modules.alerts import chain

log = structlog.get_logger()


@dataclass(frozen=True)
class Jobs:
    """Шаги эскалации со сроками из настроек."""

    sessionmaker: async_sessionmaker[AsyncSession]
    settings: Settings

    def handlers(self) -> dict[str, Entrypoint]:
        return {chain.ENTRYPOINT: Entrypoint(self.escalate)}

    async def escalate(self, payload: bytes | None) -> None:
        """Выполнить шаг одной транзакцией. Ошибка — предупреждение и повтор очередью;
        после последней попытки ошибку пишет очередь, `queue.task_failed` (спека #56, 9.8)."""
        parsed = chain.parse(payload)
        if parsed is None:
            log.error("alert.escalation_invalid_job")
            return
        alert_id, step = parsed
        try:
            async with self.sessionmaker() as session:
                await chain.run(
                    session, alert_id, step, self.settings.escalation_minutes, datetime.now(UTC)
                )
                await session.commit()
        except Exception:
            log.warning(
                "alert.escalation_attempt_failed", alert_id=str(alert_id), step=step, exc_info=True
            )
            raise


def build_jobs(settings: Settings, sessionmaker: async_sessionmaker[AsyncSession]) -> Jobs:
    return Jobs(sessionmaker=sessionmaker, settings=settings)
