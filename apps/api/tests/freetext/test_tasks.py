"""Задачи worker'а: сигналы о риске и находки как предложения (спека #32, разделы 4 и 6).

Модель — `FunctionModel`; задача вызывается напрямую, как её вызвал бы worker.
"""

import asyncio
from pathlib import Path
from uuid import uuid4

from clin_compass_knowledge.grls import Registry
from clin_compass_knowledge.loader import Knowledge
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.modules.alerts import service as alerts
from clin_compass.modules.alerts.rules import RuleBook
from clin_compass.modules.proposals import service as proposals
from tests.freetext.conftest import Care
from tests.freetext.jobs import (
    INTENT,
    NO_FINDINGS,
    NO_RISK,
    THOUGHTS,
    ai_alerts,
    load_analysis,
    make_jobs,
    patient_alerts,
    submit,
)

ALERT_RULES = Path(__file__).parents[1] / "alerts" / "knowledge"


# --- сигналы ---------------------------------------------------------------------------


async def test_risk_sign_raises_mandatory_signal_with_text_of_the_sign(
    sessionmaker: async_sessionmaker[AsyncSession],
    care: Care,
    knowledge: Knowledge,
    registry: Registry,
) -> None:
    jobs, _ = make_jobs(sessionmaker, knowledge, registry, {"signs": [THOUGHTS]})
    payload = await submit(sessionmaker, care)

    await jobs.check_risk(payload)

    [alert] = await ai_alerts(sessionmaker, care)
    analysis = await load_analysis(sessionmaker, payload)
    assert (alert.level, alert.title, alert.doctor_actions) == (
        "mandatory",
        "Тест: мысли о смерти",
        ["Тест: связаться в тот же день"],
    )
    [trigger] = alert.triggers
    assert trigger.origin_ref == str(analysis.risk_run_id)
    assert trigger.evidence["kind"] == "test-thoughts"
    assert trigger.evidence["stage"] == "check_risk"
    assert trigger.evidence["signs"] == [
        {"kind": "test-thoughts", "quote": "не хочу жить", "quote_found": True}
    ]
    assert analysis.risk_checked_at is not None
    assert analysis.alerts == {"test-thoughts": str(alert.id)}


async def test_most_severe_sign_gives_signal_title(
    sessionmaker: async_sessionmaker[AsyncSession],
    care: Care,
    knowledge: Knowledge,
    registry: Registry,
) -> None:
    jobs, _ = make_jobs(sessionmaker, knowledge, registry, {"signs": [THOUGHTS, INTENT]})

    await jobs.check_risk(await submit(sessionmaker, care))

    [alert] = await ai_alerts(sessionmaker, care)
    assert alert.title == "Тест: намерение"
    assert [s["kind"] for s in alert.triggers[0].evidence["signs"]] == [
        "test-thoughts",
        "test-intent",
    ]


async def test_no_risk_changes_no_signal(
    sessionmaker: async_sessionmaker[AsyncSession],
    care: Care,
    knowledge: Knowledge,
    registry: Registry,
) -> None:
    # LLM может только поднять сигнал: ответ «признаков нет» не трогает открытые сигналы.
    async with sessionmaker() as session:
        await alerts.check_results(
            session,
            RuleBook.load(ALERT_RULES),
            patient_id=care.patient.id,
            submitted_scale="test-mood",
            results={"test-mood": {"total": 4}},
            origin_ref="assessment:1",
        )
        await alerts.raise_signal(
            session,
            patient_id=care.patient.id,
            title="Прежний AI-сигнал",
            doctor_actions=["Связаться"],
            evidence={"kind": "test-thoughts"},
            ai_run_id=uuid4(),
        )
        await session.commit()
    before = await patient_alerts(sessionmaker, care)
    jobs, _ = make_jobs(sessionmaker, knowledge, registry, NO_RISK, NO_FINDINGS)
    payload = await submit(sessionmaker, care)

    await jobs.check_risk(payload)
    await jobs.extract(payload)

    assert await patient_alerts(sessionmaker, care) == before
    assert [(a.origin_type, a.level, a.occurrences) for a in before] == [
        ("rule", "desirable", 1),
        ("ai_run", "mandatory", 1),
    ]


async def test_same_sign_in_second_text_joins_open_signal(
    sessionmaker: async_sessionmaker[AsyncSession],
    care: Care,
    knowledge: Knowledge,
    registry: Registry,
) -> None:
    jobs, _ = make_jobs(sessionmaker, knowledge, registry, {"signs": [THOUGHTS]})

    await jobs.check_risk(await submit(sessionmaker, care))
    await jobs.check_risk(await submit(sessionmaker, care))

    [alert] = await ai_alerts(sessionmaker, care)
    assert alert.occurrences == 2


async def test_more_severe_sign_in_second_text_raises_new_signal(
    sessionmaker: async_sessionmaker[AsyncSession],
    care: Care,
    knowledge: Knowledge,
    registry: Registry,
) -> None:
    # План при открытом сигнале «мысли о смерти» не дописывается к нему молча: врач получает
    # новое событие.
    jobs, _ = make_jobs(
        sessionmaker, knowledge, registry, {"signs": [THOUGHTS]}, {"signs": [INTENT]}
    )

    await jobs.check_risk(await submit(sessionmaker, care))
    await jobs.check_risk(await submit(sessionmaker, care))

    titles = [a.title for a in await ai_alerts(sessionmaker, care)]
    assert titles == ["Тест: мысли о смерти", "Тест: намерение"]


async def test_less_severe_sign_joins_open_more_severe_signal(
    sessionmaker: async_sessionmaker[AsyncSession],
    care: Care,
    knowledge: Knowledge,
    registry: Registry,
) -> None:
    jobs, _ = make_jobs(
        sessionmaker, knowledge, registry, {"signs": [INTENT]}, {"signs": [THOUGHTS]}
    )

    await jobs.check_risk(await submit(sessionmaker, care))
    await jobs.check_risk(await submit(sessionmaker, care))

    [alert] = await ai_alerts(sessionmaker, care)
    assert (alert.title, alert.occurrences) == ("Тест: намерение", 2)


async def test_extraction_does_not_repeat_signal_of_risk_check(
    sessionmaker: async_sessionmaker[AsyncSession],
    care: Care,
    knowledge: Knowledge,
    registry: Registry,
) -> None:
    jobs, _ = make_jobs(
        sessionmaker,
        knowledge,
        registry,
        {"signs": [THOUGHTS]},
        {"findings": [], "signs": [THOUGHTS]},
    )
    payload = await submit(sessionmaker, care)
    await jobs.check_risk(payload)
    [alert] = await ai_alerts(sessionmaker, care)
    # Врач подтвердил сигнал быстрой проверки раньше, чем закончился полный разбор.
    async with sessionmaker() as session:
        await alerts.acknowledge(session, doctor_id=care.doctor.id, alert_id=alert.id)
        await session.commit()

    await jobs.extract(payload)

    [after] = await ai_alerts(sessionmaker, care)
    assert (after.id, after.occurrences) == (alert.id, 1)
    analysis = await load_analysis(sessionmaker, payload)
    assert [s["stage"] for s in analysis.risk_signs] == ["check_risk", "extract"]


async def test_extraction_raises_signal_missed_by_risk_check(
    sessionmaker: async_sessionmaker[AsyncSession],
    care: Care,
    knowledge: Knowledge,
    registry: Registry,
) -> None:
    jobs, _ = make_jobs(
        sessionmaker, knowledge, registry, NO_RISK, {"findings": [], "signs": [THOUGHTS]}
    )
    payload = await submit(sessionmaker, care)

    await jobs.check_risk(payload)
    await jobs.extract(payload)

    [alert] = await ai_alerts(sessionmaker, care)
    assert alert.triggers[0].evidence["stage"] == "extract"


# --- находки ---------------------------------------------------------------------------


async def test_findings_become_proposals_with_drug_check(
    sessionmaker: async_sessionmaker[AsyncSession],
    care: Care,
    knowledge: Knowledge,
    registry: Registry,
) -> None:
    finding = {
        "category": "adherence",
        "summary": "принимает авиандр",
        "quote": "Принимаю авиандор утром",
        "drug_name": "авиандор",
        "adherence_event": "taken",
    }
    symptom = {**finding, "category": "symptom", "summary": "плохой сон", "quote": "Сплю плохо"}
    symptom |= {"drug_name": None, "adherence_event": None}
    jobs, _ = make_jobs(
        sessionmaker, knowledge, registry, {"findings": [finding, symptom], "signs": []}
    )
    payload = await submit(sessionmaker, care)

    await jobs.extract(payload)

    analysis = await load_analysis(sessionmaker, payload)
    async with sessionmaker() as session:
        made = await proposals.list_for_patient(session, patient_id=care.patient.id)
    assert [(p.kind, p.origin_type, p.origin_ref) for p in made] == [
        ("freetext.finding", "ai_run", str(analysis.extract_run_id))
    ] * 2
    assert made[0].payload == {
        "analysis_id": str(analysis.id),
        "source_type": "diary",
        "source_id": str(analysis.source_id),
        "transcript": False,
        "category": "adherence",
        "summary": "принимает авиандр",
        "quote": "Принимаю авиандор утром",
        "drug": {
            "name": "авиандор",
            "check": "similar",
            "matches": [
                {
                    "trade_name": "АВИАНДР®",
                    "inn": "Маритупирдин",
                    "reg_number": "ЛП-№(002445)-(РГ-RU)",
                }
            ],
        },
        "adherence_event": "taken",
        "recorded_at": analysis.created_at.isoformat(),
    }
    assert made[1].payload["drug"] is None
    assert analysis.extracted_at is not None


async def test_extraction_after_acknowledged_more_severe_signal_raises_nothing(
    sessionmaker: async_sessionmaker[AsyncSession],
    care: Care,
    knowledge: Knowledge,
    registry: Registry,
) -> None:
    # Этапы разошлись в виде: быстрая проверка нашла намерение, полный разбор — только мысли.
    # Врач уже занялся более тяжёлым сигналом по этому тексту: новый сигнал был бы шумом.
    jobs, _ = make_jobs(
        sessionmaker,
        knowledge,
        registry,
        {"signs": [INTENT]},
        {"findings": [], "signs": [THOUGHTS]},
    )
    payload = await submit(sessionmaker, care)
    await jobs.check_risk(payload)
    [alert] = await ai_alerts(sessionmaker, care)
    async with sessionmaker() as session:
        await alerts.acknowledge(session, doctor_id=care.doctor.id, alert_id=alert.id)
        await session.commit()

    await jobs.extract(payload)

    assert [a.id for a in await ai_alerts(sessionmaker, care)] == [alert.id]


async def test_two_texts_at_once_raise_one_signal(
    sessionmaker: async_sessionmaker[AsyncSession],
    care: Care,
    knowledge: Knowledge,
    registry: Registry,
) -> None:
    # Проверка риска и полный разбор идут в разных слотах: два текста пациента могут дойти
    # до сигналов одновременно.
    jobs, _ = make_jobs(sessionmaker, knowledge, registry, {"signs": [THOUGHTS]})
    first, second = await submit(sessionmaker, care), await submit(sessionmaker, care)

    await asyncio.gather(jobs.check_risk(first), jobs.check_risk(second))

    [alert] = await ai_alerts(sessionmaker, care)
    assert alert.occurrences == 2
