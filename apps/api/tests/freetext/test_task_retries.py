"""Задачи worker'а: ожидание узла, повторы после ошибок, повтор задачи (спека #32, 4.4–4.5)."""

from datetime import timedelta

import pytest
from clin_compass_knowledge.grls import Registry
from clin_compass_knowledge.loader import Knowledge
from pydantic_ai.exceptions import ModelHTTPError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.core.ai.llm import MODEL_ALIAS
from clin_compass.core.ai.nodes import NodesUnavailableError
from clin_compass.core.db import make_engine, make_sessionmaker
from clin_compass.core.queue import RetryRequested
from clin_compass.modules.freetext import service as freetext
from clin_compass.modules.proposals import service as proposals
from tests.freetext.conftest import Care
from tests.freetext.jobs import NO_RISK, THOUGHTS, ai_alerts, load_analysis, make_jobs, submit


async def test_unavailable_llm_waits_without_counting_a_failure(
    sessionmaker: async_sessionmaker[AsyncSession],
    care: Care,
    knowledge: Knowledge,
    registry: Registry,
) -> None:
    jobs, _ = make_jobs(sessionmaker, knowledge, registry, NodesUnavailableError("llm"))
    payload = await submit(sessionmaker, care)

    with pytest.raises(RetryRequested) as retry:
        await jobs.check_risk(payload)

    assert retry.value.delay == timedelta(seconds=30)
    analysis = await load_analysis(sessionmaker, payload)
    assert (analysis.risk_failures, analysis.risk_failed_at) == (0, None)


async def test_failing_stage_is_retried_with_growing_delay_then_marked_failed(
    sessionmaker: async_sessionmaker[AsyncSession],
    care: Care,
    knowledge: Knowledge,
    registry: Registry,
) -> None:
    jobs, _ = make_jobs(
        sessionmaker, knowledge, registry, ModelHTTPError(status_code=500, model_name=MODEL_ALIAS)
    )
    payload = await submit(sessionmaker, care)
    delays = []
    for _ in range(4):
        with pytest.raises(RetryRequested) as retry:
            await jobs.extract(payload)
        delays.append(retry.value.delay.total_seconds())

    await jobs.extract(payload)

    assert delays == [2, 4, 8, 16]
    analysis = await load_analysis(sessionmaker, payload)
    assert analysis.extract_failures == 5
    assert analysis.extract_failed_at is not None
    async with sessionmaker() as session:
        status = await freetext.statuses(
            session, source_type="diary", source_ids=[analysis.source_id]
        )
    assert status == {analysis.source_id: "failed"}


async def test_repeated_job_after_result_does_not_call_model_again(
    sessionmaker: async_sessionmaker[AsyncSession],
    care: Care,
    knowledge: Knowledge,
    registry: Registry,
) -> None:
    jobs, script = make_jobs(
        sessionmaker,
        knowledge,
        registry,
        {"signs": [THOUGHTS]},
        {
            "findings": [
                {
                    "category": "symptom",
                    "summary": "сон",
                    "quote": "Сплю плохо",
                    "drug_name": None,
                    "adherence_event": None,
                }
            ],
            "signs": [],
        },
    )
    payload = await submit(sessionmaker, care)

    for _ in range(2):
        await jobs.check_risk(payload)
        await jobs.extract(payload)

    assert len(script.calls) == 2
    assert len(await ai_alerts(sessionmaker, care)) == 1
    async with sessionmaker() as session:
        assert len(await proposals.list_for_patient(session, patient_id=care.patient.id)) == 1


async def test_retry_carries_no_exception_chain(
    sessionmaker: async_sessionmaker[AsyncSession],
    care: Care,
    knowledge: Knowledge,
    registry: Registry,
) -> None:
    # PgQueuer пишет трассировку повтора в журнал pgq вместе с причинами. В причине могла бы
    # оказаться ошибка базы с параметрами запроса — цитатами пациента.
    jobs, _ = make_jobs(
        sessionmaker, knowledge, registry, ModelHTTPError(status_code=500, model_name=MODEL_ALIAS)
    )
    payload = await submit(sessionmaker, care)

    with pytest.raises(RetryRequested) as retry:
        await jobs.check_risk(payload)

    assert retry.value.__cause__ is None
    assert retry.value.__suppress_context__


async def test_lost_database_means_retry_not_a_plain_error(
    sessionmaker: async_sessionmaker[AsyncSession],
    care: Care,
    knowledge: Knowledge,
    registry: Registry,
) -> None:
    # Обычная ошибка из обработчика после повторов ожидания сразу стала бы последней
    # (тест очереди), и разбор навсегда остался бы «обрабатывается».
    payload = await submit(sessionmaker, care)
    dead = make_sessionmaker(make_engine("postgresql+asyncpg://compass:compass@127.0.0.1:9/none"))
    jobs, _ = make_jobs(dead, knowledge, registry, NO_RISK)

    with pytest.raises(RetryRequested) as retry:
        await jobs.check_risk(payload)

    assert retry.value.delay == timedelta(seconds=30)
