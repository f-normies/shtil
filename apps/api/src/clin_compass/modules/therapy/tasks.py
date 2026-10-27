"""Задача worker'а: варианты терапии по клиническим рекомендациям (спека #107, раздел 5).

Аргумент задачи — ключ профиля. Одна задача считает ответ для всех строк `pending` этого
ключа. Вызов модели идёт вне транзакции, ответ пишется одной транзакцией.

Повторы задача делает сама через `RetryRequested`, как `freetext`: пока узлы моделей
недоступны, она ждёт без счёта попыток. Другие ошибки считаются в строках справки; после
`FAILURE_LIMIT` неудач строки получают `failed`, и врач видит кнопку «Повторить».
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import clin_compass_knowledge
import structlog
from clin_compass_knowledge.loader import load_knowledge
from clin_compass_knowledge.schemas.source import Source
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.core.ai.nodes import NodesUnavailableError
from clin_compass.core.ai.runs import DbRunRecorder, describe_error
from clin_compass.core.config import Settings
from clin_compass.core.queue import Entrypoint, RetryRequested
from clin_compass.modules.knowledge import service as knowledge
from clin_compass.modules.therapy import guidance
from clin_compass.modules.therapy.guidance import ENTRYPOINT

log = structlog.get_logger()

# Пока Мак выключен, задача проверяет узел раз в 30 с.
UNAVAILABLE_DELAY = timedelta(seconds=30)
_FIRST_RETRY_SECONDS = 5
# Во всех процессах worker'а — не больше одной справки за раз: узел LLM нужен и freetext.
_CONCURRENCY = 1


class SourcesNotIndexedError(RuntimeError):
    """Поиск не нашёл ни одного фрагмента: источники вопроса не проиндексированы.

    Пустой ответ не сохраняется как готовый: иначе он копировался бы всем пациентам
    с тем же профилем и после индексации.
    """


class QuotesNotConfirmedError(RuntimeError):
    """Модель ответила, но проверка цитат отбросила все утверждения.

    Это сбой ответа. «В базе ответа нет» — только пустой ответ без отброшенных утверждений.
    Пустой ответ после сбоя не сохраняется как готовый: иначе он копировался бы всем
    пациентам с тем же профилем.
    """


@dataclass(frozen=True)
class Jobs:
    """Обработчик справки: клиенты моделей и источники манифеста для названий КР."""

    sessionmaker: async_sessionmaker[AsyncSession]
    ai: knowledge.KnowledgeAi
    sources: Mapping[str, Source]

    def handlers(self) -> dict[str, Entrypoint]:
        """Точки входа для `worker.build_handlers`."""
        return {ENTRYPOINT: Entrypoint(self.guidance, concurrency_limit=_CONCURRENCY)}

    async def guidance(self, payload: bytes | None) -> None:
        """Справка по ключу профиля; наружу выходит только `RetryRequested`.

        Обычная ошибка из обработчика могла бы стать последней попыткой PgQueuer без нашей
        отметки `failed` (`core/queue`). Повтор поднимается без цепочки причин: в ошибке
        бывают параметры запроса. В логах — только ключ и класс ошибки, текста вопроса нет.
        """
        key = _key(payload)
        try:
            await self._compute(key)
        except NodesUnavailableError:
            raise RetryRequested(delay=UNAVAILABLE_DELAY, reason="ai nodes unavailable") from None
        except Exception as exc:  # noqa: BLE001 — любая ошибка: повтор или отметка о неудаче
            reason = describe_error(exc)
            try:
                counts = await self._record_failure(key)
            except Exception as record_exc:  # noqa: BLE001 — см. выше
                # База worker'а недоступна: ждём её так же, как узел модели.
                log.warning(
                    "therapy.guidance_failure_not_recorded",
                    profile_key=key,
                    error=reason,
                    record_error=describe_error(record_exc),
                )
                raise RetryRequested(delay=UNAVAILABLE_DELAY, reason=reason) from None
            log.warning("therapy.guidance_error", profile_key=key, error=reason)
            if not counts:
                # Строки ключа уже посчитаны или упали: ни считать, ни отмечать нечего.
                log.info("therapy.guidance_no_pending", profile_key=key)
                return
            failed = sum(c >= guidance.FAILURE_LIMIT for c in counts)
            if failed:
                log.error("therapy.guidance_failed", profile_key=key, rows=failed)
            remaining = [c for c in counts if c < guidance.FAILURE_LIMIT]
            if remaining:
                delay = timedelta(seconds=_FIRST_RETRY_SECONDS * 2 ** (max(remaining) - 1))
                raise RetryRequested(delay=delay, reason=reason) from None

    async def _compute(self, key: str) -> None:
        async with self.sessionmaker() as session:
            # Ответ с этим ключом уже готов (повтор задачи или другой пациент): копия.
            if await guidance.copy_ready(session, key):
                await session.commit()
                return
            profile = await guidance.pending_profile(session, key)
        if profile is None:
            return
        answer = await knowledge.answer(
            self.sessionmaker,
            self.ai,
            guidance.question(profile, self.sources),
            context=guidance.context(profile),
            source_ids=list(profile.source_ids),
        )
        if answer.run_id is None:
            raise SourcesNotIndexedError
        if not answer.claims and answer.dropped:
            raise QuotesNotConfirmedError
        async with self.sessionmaker() as session:
            await guidance.save(session, key, answer)
            await session.commit()

    async def _record_failure(self, key: str) -> list[int]:
        """Счётчики неудач в своей транзакции; возвращает новые счётчики строк ключа."""
        async with self.sessionmaker() as session:
            counts = await guidance.record_failure(session, key)
            await session.commit()
        return counts


def build_jobs(settings: Settings, sessionmaker: async_sessionmaker[AsyncSession]) -> Jobs:
    """Задача с клиентами `COMPASS_EMBEDDINGS_URLS`, `COMPASS_LLM_URLS` и манифестом knowledge/.

    Каждый вызов модели пишется в ai_runs задачами `knowledge.search` и `knowledge.answer`.

    Raises:
        AiNotConfiguredError: Не заданы адреса узлов эмбеддингов или LLM.
        KnowledgeError: Данные knowledge/ не прошли проверку.
    """
    ai = knowledge.build_ai(settings, DbRunRecorder(sessionmaker))
    root = settings.knowledge_dir or _repository_knowledge_dir()
    return Jobs(sessionmaker, ai, load_knowledge(root).sources)


def _repository_knowledge_dir() -> Path:
    """knowledge/ рабочей копии, как в `freetext.tasks`: пакет лежит в knowledge/src/…"""
    return Path(clin_compass_knowledge.__file__).resolve().parents[2]


def _key(payload: bytes | None) -> str:
    if payload is None:
        raise ValueError("therapy guidance job without profile key")
    return payload.decode()
