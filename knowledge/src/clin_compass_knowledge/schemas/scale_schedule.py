"""Расписание опросов пациента: `scale-schedule.yaml` (спека #111)."""

from typing import Annotated

from pydantic import Field

from clin_compass_knowledge.schemas.common import Id, Model, NonEmpty, SourceRef, Status


class ScheduledScale(Model):
    """Шкала по расписанию: следующий опрос — через `period_days` после ответа."""

    scale: Id
    period_days: int = Field(ge=1)
    source: SourceRef  # почему такой период: обычно период вопроса в инструкции шкалы


class ScaleSchedule(Model):
    """Файл `scale-schedule.yaml`."""

    version: int = Field(ge=1)
    status: Status
    # Ответа нет к концу дня «плановый + grace_days» — пропуск на графике врача.
    grace_days: int = Field(ge=0)
    grace_basis: NonEmpty  # обоснование срока: норматива в КР нет
    # Периоды, из которых врач выбирает частоту опроса своего пациента (спека #180).
    allowed_period_days: tuple[Annotated[int, Field(ge=1)], ...]
    allowed_period_basis: NonEmpty  # почему врачу доступны именно эти периоды
    # Без min_length: pydantic добавляет к ошибке единственного элемента ещё и «минимум 1».
    # Пустой список — опросов по расписанию нет; PHQ-9 и GAD-7 проверяет тест репозитория.
    scales: tuple[ScheduledScale, ...]
