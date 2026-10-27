"""Задачи worker'а: быстрая проверка риска и полный разбор текста (спека #32, раздел 4).

Вызов модели идёт вне транзакции. Результат пишется одной транзакцией под блокировкой
записи разбора: если worker упал после коммита, повтор задачи увидит готовый этап и выйдет.

Повторы задача делает сама через `RetryRequested`: пока все узлы LLM недоступны, она ждёт
без счёта попыток. Другие ошибки считаются в записи разбора; после `FAILURE_LIMIT` этап
получает отметку `*_failed_at`, и врач видит статус «не разобрано».
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Literal
from uuid import UUID

import clin_compass_knowledge
import structlog
from clin_compass_knowledge.grls import load_registry
from clin_compass_knowledge.loader import load_knowledge
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.core.ai.llm import Llm, build_llm_model
from clin_compass.core.ai.nodes import NodesUnavailableError
from clin_compass.core.ai.runs import DbRunRecorder, describe_error
from clin_compass.core.config import Settings
from clin_compass.core.queue import Entrypoint, RetryRequested
from clin_compass.modules.freetext.analysis import Analyzer, FoundSign, TextFinding
from clin_compass.modules.freetext.models import Analysis
from clin_compass.modules.freetext.service import (
    EXTRACT_ENTRYPOINT,
    PROPOSAL_KIND,
    RISK_ENTRYPOINT,
)
from clin_compass.modules.freetext.signals import raise_for_signs
from clin_compass.modules.proposals import service as proposals

log = structlog.get_logger()

Stage = Literal["check_risk", "extract"]
FAILURE_LIMIT = 5
# Пока Мак выключен, задача проверяет узел раз в 30 с.
UNAVAILABLE_DELAY = timedelta(seconds=30)
_FIRST_RETRY_SECONDS = 2
# Узел LLM держит 2 запроса одновременно (ADR-0006): по одному слоту на этап, и проверка
# риска не ждёт полных разборов.
_CONCURRENCY = 1


@dataclass(frozen=True)
class Jobs:
    """Обработчики двух задач с общими зависимостями."""

    sessionmaker: async_sessionmaker[AsyncSession]
    analyzer: Analyzer

    def handlers(self) -> dict[str, Entrypoint]:
        """Точки входа для `worker.build_handlers`."""
        return {
            RISK_ENTRYPOINT: Entrypoint(self.check_risk, concurrency_limit=_CONCURRENCY),
            EXTRACT_ENTRYPOINT: Entrypoint(self.extract, concurrency_limit=_CONCURRENCY),
        }

    async def check_risk(self, payload: bytes | None) -> None:
        """Быстрая проверка риска; найденный признак сразу поднимает сигнал."""
        await self._run(_analysis_id(payload), "check_risk", self._check_risk)

    async def extract(self, payload: bytes | None) -> None:
        """Полный разбор: находки — предложения врачу, признаки риска — сигналы."""
        await self._run(_analysis_id(payload), "extract", self._extract)

    async def _check_risk(self, analysis_id: UUID) -> None:
        analysis = await self._pending(analysis_id, "check_risk")
        if analysis is None:
            return
        result = await self.analyzer.check_risk(
            analysis.text, source_type=_source(analysis), transcript=analysis.transcript
        )
        async with self.sessionmaker() as session:
            locked = await _lock(session, analysis_id)
            if _done(locked, "check_risk"):
                return
            locked.risk_signs = [*locked.risk_signs, *_signs(result.signs, "check_risk")]
            await raise_for_signs(
                session,
                self.analyzer.signs,
                self.analyzer.sources,
                locked,
                result.signs,
                stage="check_risk",
                run_id=result.run_id,
            )
            locked.risk_checked_at = func.now()
            locked.risk_run_id = result.run_id
            await session.commit()

    async def _extract(self, analysis_id: UUID) -> None:
        analysis = await self._pending(analysis_id, "extract")
        if analysis is None:
            return
        result = await self.analyzer.extract(
            analysis.text, source_type=_source(analysis), transcript=analysis.transcript
        )
        async with self.sessionmaker() as session:
            locked = await _lock(session, analysis_id)
            if _done(locked, "extract"):
                return
            for finding in result.findings:
                await proposals.create_proposal(
                    session,
                    patient_id=locked.patient_id,
                    kind=PROPOSAL_KIND,
                    payload=finding_payload(locked, finding),
                    origin_type="ai_run",
                    origin_ref=str(result.run_id),
                )
            locked.risk_signs = [*locked.risk_signs, *_signs(result.signs, "extract")]
            await raise_for_signs(
                session,
                self.analyzer.signs,
                self.analyzer.sources,
                locked,
                result.signs,
                stage="extract",
                run_id=result.run_id,
            )
            locked.extracted_at = func.now()
            locked.extract_run_id = result.run_id
            await session.commit()

    async def _pending(self, analysis_id: UUID, stage: Stage) -> Analysis | None:
        """Запись разбора, если этап ещё не завершён."""
        async with self.sessionmaker() as session:
            analysis = await session.get(Analysis, analysis_id)
        if analysis is None:
            log.warning("freetext.analysis_missing", analysis_id=str(analysis_id), stage=stage)
            return None
        return None if _done(analysis, stage) else analysis

    async def _run(
        self, analysis_id: UUID, stage: Stage, work: Callable[[UUID], Awaitable[None]]
    ) -> None:
        """Этап целиком; наружу выходит только `RetryRequested`.

        Ожидание через RetryRequested увеличивает счётчик попыток PgQueuer, и обычная ошибка
        после него стала бы последней без нашей отметки `*_failed_at` (`core/queue`). Повтор
        поднимается без цепочки причин (`from None`): PgQueuer пишет трассировку в журнал pgq,
        а в ошибке базы бывают параметры запроса — цитаты пациента.
        """
        try:
            await work(analysis_id)
        except NodesUnavailableError:
            raise RetryRequested(delay=UNAVAILABLE_DELAY, reason="llm nodes unavailable") from None
        # Любая ошибка этапа — повтор или отметка о неудаче: обычная ошибка из обработчика
        # могла бы стать последней попыткой PgQueuer без нашей отметки.
        except Exception as exc:  # noqa: BLE001
            reason = describe_error(exc)
            try:
                failures = await self._record_failure(analysis_id, stage)
            except Exception as record_exc:  # noqa: BLE001 — см. выше
                # База worker'а недоступна: ждём её так же, как узел модели.
                log.warning(
                    "freetext.failure_not_recorded",
                    analysis_id=str(analysis_id),
                    stage=stage,
                    error=reason,
                    record_error=describe_error(record_exc),
                )
                raise RetryRequested(delay=UNAVAILABLE_DELAY, reason=reason) from None
            # Текст ошибки не пишем: в нём может повториться текст пациента.
            log.warning(
                "freetext.stage_error",
                analysis_id=str(analysis_id),
                stage=stage,
                error=reason,
                failures=failures,
            )
            if failures < FAILURE_LIMIT:
                delay = timedelta(seconds=_FIRST_RETRY_SECONDS * 2 ** (failures - 1))
                raise RetryRequested(delay=delay, reason=reason) from None
            log.error("freetext.stage_failed", analysis_id=str(analysis_id), stage=stage)

    async def _record_failure(self, analysis_id: UUID, stage: Stage) -> int:
        """Счётчик неудач этапа +1 в своей транзакции; на последней — отметка `*_failed_at`."""
        failures = Analysis.risk_failures if stage == "check_risk" else Analysis.extract_failures
        failed_at = "risk_failed_at" if stage == "check_risk" else "extract_failed_at"
        async with self.sessionmaker() as session:
            count: int | None = await session.scalar(
                update(Analysis)
                .where(Analysis.id == analysis_id)
                .values({failures: failures + 1})
                .returning(failures)
            )
            if count is None:
                raise RuntimeError(f"analysis {analysis_id} disappeared")
            if count >= FAILURE_LIMIT:
                await session.execute(
                    update(Analysis)
                    .where(Analysis.id == analysis_id)
                    .values({failed_at: func.now()})
                )
            await session.commit()
        return count


def build_jobs(settings: Settings, sessionmaker: async_sessionmaker[AsyncSession]) -> Jobs:
    """Задачи с моделью из узлов `COMPASS_LLM_URLS`, видами признаков и снимком ГРЛС.

    Raises:
        AiNotConfiguredError: Адреса узлов LLM не заданы.
        KnowledgeError: Данные knowledge/ не прошли проверку.
        ValueError: Нет ни одного вида признаков риска `reviewed`.
    """
    model = build_llm_model(settings)
    root = settings.knowledge_dir or _repository_knowledge_dir()
    knowledge = load_knowledge(root)
    analyzer = Analyzer(
        Llm(model, DbRunRecorder(sessionmaker)),
        knowledge.signs.values(),
        load_registry(root / "sources" / "files" / "grls.csv"),
        knowledge.manifest,
    )
    return Jobs(sessionmaker, analyzer)


def _repository_knowledge_dir() -> Path:
    """knowledge/ рабочей копии, как в `alerts.rules`: пакет лежит в knowledge/src/…"""
    return Path(clin_compass_knowledge.__file__).resolve().parents[2]


def finding_payload(analysis: Analysis, finding: TextFinding) -> dict[str, object]:
    """`payload` предложения `freetext.finding` (спека #32, раздел 3.3).

    `recorded_at` — время записи дневника или сообщения (спека #96, раздел 3.3). Его передаёт
    `submit_text`, если разбор поставлен позже записи. Иначе разбор создан в одной транзакции
    с записью, и его время — время записи.
    """
    drug = finding.drug
    return {
        "analysis_id": str(analysis.id),
        "source_type": analysis.source_type,
        "source_id": str(analysis.source_id),
        "transcript": analysis.transcript,
        "category": finding.category,
        "summary": finding.summary,
        "quote": finding.quote,
        "drug": None
        if drug is None
        else {
            "name": drug.name,
            "check": drug.check,
            "matches": [
                {"trade_name": m.trade_name, "inn": m.inn, "reg_number": m.reg_number}
                for m in drug.matches
            ],
        },
        "adherence_event": finding.adherence_event,
        "recorded_at": (analysis.recorded_at or analysis.created_at).isoformat(),
    }


def _signs(signs: tuple[FoundSign, ...], stage: Stage) -> list[dict[str, object]]:
    return [
        {"kind": s.kind, "quote": s.quote, "quote_found": s.quote_found, "stage": stage}
        for s in signs
    ]


def _done(analysis: Analysis, stage: Stage) -> bool:
    if stage == "check_risk":
        return analysis.risk_checked_at is not None or analysis.risk_failed_at is not None
    return analysis.extracted_at is not None or analysis.extract_failed_at is not None


async def _lock(session: AsyncSession, analysis_id: UUID) -> Analysis:
    analysis = await session.scalar(
        select(Analysis)
        .where(Analysis.id == analysis_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if analysis is None:
        raise RuntimeError(f"analysis {analysis_id} disappeared")
    return analysis


def _analysis_id(payload: bytes | None) -> UUID:
    if payload is None:
        raise ValueError("freetext job without analysis id")
    return UUID(payload.decode())


def _source(analysis: Analysis) -> Literal["diary", "message"]:
    return "diary" if analysis.source_type == "diary" else "message"
