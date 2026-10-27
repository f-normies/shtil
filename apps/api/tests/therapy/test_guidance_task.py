"""Задача worker'а `therapy.guidance` на настоящей БД (спека #107, раздел 5).

LLM — `FunctionModel` через `Script`, эмбеддинги — «мешок слов». Задача вызывается напрямую:
её аргумент — ключ профиля из очереди.
"""

from datetime import timedelta

import pytest
from pydantic_ai.exceptions import ModelHTTPError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from structlog.testing import capture_logs

from clin_compass.core.ai.llm import MODEL_ALIAS
from clin_compass.core.ai.nodes import NodesUnavailableError
from clin_compass.core.config import Settings
from clin_compass.core.queue import Entrypoint, RetryRequested
from clin_compass.modules.therapy.tasks import Jobs
from clin_compass.worker import build_handlers
from tests.patients.test_access import login, register_patient
from tests.therapy.conftest import DOCTOR, Care
from tests.therapy.guidance_helpers import (
    ANSWER,
    ENTRYPOINT,
    GUIDELINE_TITLE,
    finish_jobs,
    guidance,
    index_sources,
    jobs,
    make_jobs,
    rows,
)
from tests.therapy.test_api import GAD, add_diagnosis, set_profile

MODEL_ERROR = ModelHTTPError(status_code=500, model_name=MODEL_ALIAS)
# Ни одна цитата не подтверждает утверждение: числа 40 во фрагменте нет, частей 5–6 нет.
UNSUPPORTED = {
    "claims": [
        {"statement": "Рекомендуется 40 мг в сутки.", "fragment": "F1", "first": 1, "last": 1},
        {"statement": "Рекомендуется терапия.", "fragment": "F1", "first": 5, "last": 6},
    ]
}


async def _ready(care: Care, maker: async_sessionmaker[AsyncSession]) -> bytes:
    """Профиль и F41.1 внесены, задача в очереди. Возвращает её аргумент — ключ профиля."""
    await set_profile(care)
    await add_diagnosis(care)
    [(key, _, _)] = await jobs(maker)
    return key.encode()


async def _fail(jobs_: Jobs, payload: bytes, times: int) -> list[float]:
    """Запустить задачу `times` раз; паузы повторов, которые она запросила."""
    delays = []
    for _ in range(times):
        try:
            await jobs_.guidance(payload)
        except RetryRequested as retry:
            delays.append(retry.delay.total_seconds())
    return delays


async def test_task_saves_claims_with_levels_and_records_the_model_call(
    care: Care, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    await index_sources(sessionmaker)
    payload = await _ready(care, sessionmaker)
    jobs_, _ = make_jobs(sessionmaker, ANSWER)

    await jobs_.guidance(payload)

    [row] = await rows(sessionmaker)
    assert (row["status"], row["failures"]) == ("done", 0)
    assert row["computed_at"] is not None
    async with sessionmaker() as session:
        [(run_id, task, status)] = (
            await session.execute(text("SELECT id, task, status FROM ai.ai_runs"))
        ).all()
    assert (task, status) == ("knowledge.answer", "ok")
    assert row["run_id"] == run_id
    shown = await guidance(care)
    assert (shown["status"], shown["stale"]) == ("done", False)
    assert shown["computed_at"] is not None
    assert shown["profile"] == {"diagnoses": ["F41.1"], "sex": "female", "age_group": "18–64"}
    [claim] = shown["claims"]
    assert claim["quote"] == (
        "Пациентам с генерализованным тревожным расстройством рекомендуется "
        "когнитивно-поведенческая терапия."
    )
    fragment = claim["fragment"]
    assert (fragment["source_id"], fragment["title"]) == ("test-guideline", GUIDELINE_TITLE)
    assert (fragment["uur"], fragment["udd"]) == ("A", 1)


async def test_prompt_has_codes_guideline_and_group_but_no_personal_data(
    care: Care, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    await index_sources(sessionmaker)
    payload = await _ready(care, sessionmaker)
    jobs_, script = make_jobs(sessionmaker, ANSWER)

    await jobs_.guidance(payload)

    [call] = script.calls
    assert f"рекомендованы взрослому при F41.1 ({GUIDELINE_TITLE})?" in call.prompt
    assert "Пол: женский.\nВозраст: 18–64 года." in call.prompt
    # Название диагноза пишет врач: оно может нести сведения о пациенте, в вопрос идёт только код.
    assert GAD["title"] != GUIDELINE_TITLE
    for personal in ("Пациент Тестовый", "patient@example.com", "1992", "01.05", GAD["title"]):
        assert personal not in call.prompt


async def test_second_patient_with_same_profile_gets_a_copy_without_model(
    care: Care, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    await index_sources(sessionmaker)
    payload = await _ready(care, sessionmaker)
    jobs_, script = make_jobs(sessionmaker, ANSWER)
    await jobs_.guidance(payload)
    await finish_jobs(sessionmaker)
    second = await register_patient(care.client, DOCTOR, "second@example.com")
    await login(care.client, DOCTOR)
    other = Care(care.client, second)

    await set_profile(other)
    await add_diagnosis(other)

    first_row, second_row = await rows(sessionmaker)
    assert second_row["status"] == "done"
    assert (second_row["answer"], second_row["run_id"]) == (
        first_row["answer"],
        first_row["run_id"],
    )
    assert len(script.calls) == 1
    assert await jobs(sessionmaker) == []
    assert len((await guidance(other))["claims"]) == 1


async def test_task_copies_ready_answer_to_pending_rows(
    care: Care, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    await index_sources(sessionmaker)
    payload = await _ready(care, sessionmaker)
    jobs_, script = make_jobs(sessionmaker, ANSWER)
    await jobs_.guidance(payload)
    second = await register_patient(care.client, DOCTOR, "second@example.com")
    # Строка второго пациента с тем же ключом появилась до ответа: она ждёт ту же задачу.
    async with sessionmaker() as session:
        await session.execute(
            text(
                "INSERT INTO therapy.guidance (patient_id, profile_key, profile, status)"
                " SELECT :patient, profile_key, profile, 'pending' FROM therapy.guidance"
            ),
            {"patient": second},
        )
        await session.commit()

    await jobs_.guidance(payload)

    assert [r["status"] for r in await rows(sessionmaker)] == ["done", "done"]
    assert len(script.calls) == 1


async def test_unavailable_node_keeps_the_row_pending(
    care: Care, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    await index_sources(sessionmaker)
    payload = await _ready(care, sessionmaker)
    jobs_, _ = make_jobs(sessionmaker, NodesUnavailableError("llm"))

    with pytest.raises(RetryRequested) as retry:
        await jobs_.guidance(payload)

    assert retry.value.delay == timedelta(seconds=30)
    [row] = await rows(sessionmaker)
    assert (row["status"], row["failures"]) == ("pending", 0)


async def test_sources_missing_from_index_fail_after_three_attempts(
    care: Care, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    payload = await _ready(care, sessionmaker)
    jobs_, script = make_jobs(sessionmaker, ANSWER)

    delays = await _fail(jobs_, payload, 3)

    assert len(delays) == 2
    assert delays[0] < delays[1]
    [row] = await rows(sessionmaker)
    assert (row["status"], row["failures"], row["answer"]) == ("failed", 3, None)
    assert script.calls == []


async def test_answer_whose_quotes_all_failed_the_check_is_a_failure(
    care: Care, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    await index_sources(sessionmaker)
    payload = await _ready(care, sessionmaker)
    jobs_, script = make_jobs(sessionmaker, UNSUPPORTED)

    with capture_logs() as logs:
        delays = await _fail(jobs_, payload, 3)

    assert len(delays) == 2
    [row] = await rows(sessionmaker)
    assert (row["status"], row["failures"], row["answer"]) == ("failed", 3, None)
    assert len(script.calls) == 3
    assert [e["event"] for e in logs if e["event"] == "therapy.guidance_failed"] == [
        "therapy.guidance_failed"
    ]


async def test_empty_answer_without_dropped_claims_is_done(
    care: Care, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    await index_sources(sessionmaker)
    payload = await _ready(care, sessionmaker)
    jobs_, _ = make_jobs(sessionmaker, {"claims": []})

    await jobs_.guidance(payload)

    [row] = await rows(sessionmaker)
    assert (row["status"], row["failures"]) == ("done", 0)
    assert (row["answer"]["claims"], row["answer"]["dropped"]) == ([], 0)
    shown = await guidance(care)
    assert (shown["status"], shown["claims"]) == ("done", [])


async def test_failure_without_pending_rows_is_not_logged_as_failed(
    care: Care, sessionmaker: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = await _ready(care, sessionmaker)
    jobs_, _ = make_jobs(sessionmaker, ANSWER)
    # Источники не в индексе: после трёх попыток строка ключа — `failed`.
    await _fail(jobs_, payload, 3)

    async def broken(self: Jobs, key: str) -> None:
        raise RuntimeError("ошибка после того, как строки ключа перестали ждать расчёта")

    monkeypatch.setattr(Jobs, "_compute", broken)
    with capture_logs() as logs:
        await jobs_.guidance(payload)

    events = [e["event"] for e in logs]
    assert "therapy.guidance_failed" not in events
    assert "therapy.guidance_no_pending" in events
    [row] = await rows(sessionmaker)
    assert (row["status"], row["failures"]) == ("failed", 3)


async def test_failed_row_gets_ready_answer_of_the_same_profile_on_get(
    care: Care, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    await index_sources(sessionmaker)
    payload = await _ready(care, sessionmaker)
    failing, _ = make_jobs(sessionmaker, MODEL_ERROR)
    await _fail(failing, payload, 3)
    await finish_jobs(sessionmaker)
    # Второй пациент с тем же профилем: его расчёт удался.
    second = await register_patient(care.client, DOCTOR, "second@example.com")
    await login(care.client, DOCTOR)
    other = Care(care.client, second)
    await set_profile(other)
    await add_diagnosis(other)
    working, script = make_jobs(sessionmaker, ANSWER)
    await working.guidance(payload)

    shown = await guidance(care)

    assert (shown["status"], shown["stale"]) == ("done", False)
    assert len(shown["claims"]) == 1
    first_row, second_row = await rows(sessionmaker)
    assert (first_row["status"], first_row["run_id"]) == ("done", second_row["run_id"])
    assert len(script.calls) == 1


async def test_three_model_errors_give_failed_and_retry_requeues(
    care: Care, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    await index_sources(sessionmaker)
    payload = await _ready(care, sessionmaker)
    failing, _ = make_jobs(sessionmaker, MODEL_ERROR)

    assert len(await _fail(failing, payload, 3)) == 2
    shown = await guidance(care)
    assert (shown["status"], shown["stale"], shown["claims"]) == ("failed", False, [])
    await finish_jobs(sessionmaker)

    retried = await care.client.post(f"{care.base}/therapy/guidance/retry")

    assert retried.status_code == 200
    assert retried.json()["status"] == "pending"
    assert len(await jobs(sessionmaker)) == 1
    working, _ = make_jobs(sessionmaker, ANSWER)
    await working.guidance(payload)
    assert (await guidance(care))["status"] == "done"
    done = await care.client.post(f"{care.base}/therapy/guidance/retry")
    assert done.status_code == 409


async def test_after_diagnosis_change_previous_answer_is_shown_as_stale(
    care: Care, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    await index_sources(sessionmaker)
    payload = await _ready(care, sessionmaker)
    jobs_, _ = make_jobs(sessionmaker, ANSWER)
    await jobs_.guidance(payload)

    await add_diagnosis(care, {"icd10": "F32.1", "title": "Депрессивный эпизод"})
    shown = await guidance(care)

    assert (shown["status"], shown["stale"]) == ("pending", True)
    assert len(shown["claims"]) == 1
    assert shown["computed_at"] is not None
    # Профиль показанного ответа — прежний, без нового диагноза.
    assert shown["profile"] == {"diagnoses": ["F41.1"], "sex": "female", "age_group": "18–64"}


def test_worker_registers_guidance_with_one_slot(
    settings: Settings, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    configured = settings.model_copy(
        update={
            "llm_urls": ("http://127.0.0.1:8000",),
            "embeddings_urls": ("http://127.0.0.1:8001",),
        }
    )

    handlers = build_handlers(configured, sessionmaker)

    entry = handlers[ENTRYPOINT]
    assert isinstance(entry, Entrypoint)
    assert entry.concurrency_limit == 1


def test_worker_without_embeddings_leaves_guidance_in_queue(
    settings: Settings, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    configured = settings.model_copy(update={"llm_urls": ("http://127.0.0.1:8000",)})

    assert ENTRYPOINT not in build_handlers(configured, sessionmaker)
