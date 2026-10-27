"""Ограничения БД модуля alerts: история и сигналы не удаляются, поля согласованы (спека #12, 4).

Сервис таких запросов не делает. Тесты проверяют, что их не пропустит и сама БД:
это медицинские данные.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.modules.alerts.rules import RuleBook
from tests.alerts.conftest import SELF_HARM, Care, check


async def execute(maker: async_sessionmaker[AsyncSession], sql: str, **params: object) -> None:
    async with maker() as session:
        await session.execute(text(sql), params)
        await session.commit()


@pytest.mark.parametrize(
    ("sql", "message"),
    [
        ("UPDATE alerts.triggers SET evidence = '{}'", "alerts.triggers .* UPDATE is forbidden"),
        ("DELETE FROM alerts.triggers", "alerts.triggers .* DELETE is forbidden"),
        ("TRUNCATE alerts.triggers", "alerts.triggers .* TRUNCATE is forbidden"),
        ("DELETE FROM alerts.alerts", "alerts.alerts .* DELETE is forbidden"),
        ("TRUNCATE alerts.alerts CASCADE", "TRUNCATE is forbidden"),
    ],
)
async def test_history_and_signals_cannot_be_changed_or_deleted(
    sessionmaker: async_sessionmaker[AsyncSession],
    rules: RuleBook,
    care: Care,
    sql: str,
    message: str,
) -> None:
    await check(sessionmaker, rules, care.patient.id, SELF_HARM)

    with pytest.raises(DBAPIError, match=message):
        await execute(sessionmaker, sql)


async def test_ai_signal_cannot_carry_rule_id(
    sessionmaker: async_sessionmaker[AsyncSession], care: Care
) -> None:
    with pytest.raises(IntegrityError, match="ck_alerts_rule_matches_origin"):
        await execute(
            sessionmaker,
            """
            INSERT INTO alerts.alerts
                (patient_id, doctor_id, origin_type, rule_id, kind, level, title,
                 doctor_actions, sources)
            VALUES (:patient, :doctor, 'ai_run', 'test-self-harm', 'alert', 'mandatory', 'x',
                    '["Связаться с пациентом"]', '[]')
            """,
            patient=care.patient.id,
            doctor=care.doctor.id,
        )


async def test_trigger_needs_origin_ref(
    sessionmaker: async_sessionmaker[AsyncSession], rules: RuleBook, care: Care
) -> None:
    with pytest.raises(IntegrityError, match="ck_triggers_origin_ref"):
        await check(sessionmaker, rules, care.patient.id, SELF_HARM, origin_ref="")
