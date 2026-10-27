"""Время доставки алерта в метрике (спека #40, 3.3): одно наблюдение на алерт, `origin` и `via`."""

from uuid import UUID

from prometheus_client import REGISTRY
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.modules.alerts import service as alerts
from clin_compass.modules.alerts.rules import RuleBook
from clin_compass.modules.alerts.schemas import DeliveryChannel
from tests.alerts.conftest import SELF_HARM, Care, check
from tests.alerts.test_raise_signal import raise_signal


def deliveries(origin: str, via: str) -> float:
    labels = {"origin": origin, "via": via}
    return REGISTRY.get_sample_value("compass_alert_delivery_seconds_count", labels) or 0.0


async def deliver(
    maker: async_sessionmaker[AsyncSession], care: Care, alert_id: UUID, via: DeliveryChannel
) -> None:
    async with maker() as session:
        await alerts.mark_delivered(session, doctor_id=care.doctor.id, alert_id=alert_id, via=via)
        await session.commit()


async def test_rule_alert_delivery_is_observed_once(
    sessionmaker: async_sessionmaker[AsyncSession], rules: RuleBook, care: Care
) -> None:
    [alert] = await check(sessionmaker, rules, care.patient.id, SELF_HARM)
    before = deliveries("rule", "ws")

    await deliver(sessionmaker, care, alert.id, "ws")
    await deliver(sessionmaker, care, alert.id, "ws")
    await deliver(sessionmaker, care, alert.id, "rest")

    assert deliveries("rule", "ws") == before + 1


async def test_ai_alert_delivery_is_observed_with_ai_origin(
    sessionmaker: async_sessionmaker[AsyncSession], care: Care
) -> None:
    alert = await raise_signal(sessionmaker, care)
    before = deliveries("ai", "rest")

    await deliver(sessionmaker, care, alert.id, "rest")

    assert deliveries("ai", "rest") == before + 1
