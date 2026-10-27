"""Экран помощи пациенту (спека #57): данные из knowledge/ и признак сигнала по отправке."""

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from clin_compass.modules.alerts.models import Alert, Trigger
from clin_compass.modules.alerts.rules import RuleBook
from clin_compass.modules.alerts.schemas import HelpContactOut, HelpTriggerOut, PatientHelpOut


def patient_help_view(rules: RuleBook) -> PatientHelpOut | None:
    """Текст, номера и условия правил `kind: alert`. Файла нет или черновик — None.

    Экран открывают все правила `kind: alert`: по определению это критическое состояние.
    Отдельного признака у правила нет, порогов в PWA тоже.
    """
    data = rules.patient_help
    if data is None:
        return None
    return PatientHelpOut(
        version=data.version,
        checked_on=data.checked_on,
        title=data.title,
        text=data.text,
        sources=rules.snapshot(data.sources),
        contacts=[
            HelpContactOut(
                id=c.id,
                number=c.number,
                label=c.label,
                primary=c.primary,
                sources=rules.snapshot(c.sources),
            )
            for c in data.contacts
        ],
        triggers=[
            HelpTriggerOut(rule_id=r.id, rule_version=r.version, when=r.when)
            for r in rules.rules
            if r.kind == "alert"
        ],
    )


async def alert_raised_by(session: AsyncSession, origin_ref: str) -> bool:
    """Есть ли у отправки `origin_ref` срабатывание сигнала `kind: alert`.

    Срабатывание пишется и в новый сигнал, и в открытый сигнал того же правила. Поэтому
    ответ верен и при повторной отправке: PWA узнаёт о сигнале, даже если первый ответ
    потерялся в сети.
    """
    query = select(
        exists()
        .where(Trigger.origin_ref == origin_ref, Trigger.alert_id == Alert.id)
        .where(Alert.kind == "alert")
    )
    return bool(await session.scalar(query))
