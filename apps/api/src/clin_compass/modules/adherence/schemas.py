"""Публичные схемы модуля adherence (спека #34, разделы 4 и 5)."""

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, NonNegativeInt, model_validator

from clin_compass.modules.adherence.doses import CourseState, DoseStatus


class DoseOut(BaseModel):
    """Доза дня для пациента: что принять и как отмечено.

    `due` — время приёма пришло: только такую пустую дозу «Готово» сохраняет как пропущенную.
    """

    prescription_id: UUID
    trade_name: str
    dose_mg: float
    slot: int
    slot_label: str
    status: DoseStatus | None
    marked_at: datetime | None
    due: bool


class DayDosesOut(BaseModel):
    day: date
    doses: list[DoseOut]


class MarkIn(BaseModel):
    """Отметка дозы. `status: None` снимает отметку."""

    model_config = ConfigDict(frozen=True)

    prescription_id: UUID
    day: date
    slot: NonNegativeInt
    status: DoseStatus | None


class MarksIn(BaseModel):
    """Отметки пишутся вместе: ошибка в одной — не пишется ни одна."""

    marks: list[MarkIn] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def _one_mark_per_dose(self) -> "MarksIn":
        keys = [(m.prescription_id, m.day, m.slot) for m in self.marks]
        if len(set(keys)) != len(keys):
            raise ValueError("one mark per dose")
        return self


class PastMarkIn(BaseModel):
    """Отметка с заданным временем — только для сида демо (спека #34, раздел 8)."""

    model_config = ConfigDict(frozen=True)

    day: date
    slot: NonNegativeInt
    status: DoseStatus
    marked_at: datetime


class PeriodOut(BaseModel):
    start: date
    end: date  # включительно


class CourseOut(BaseModel):
    """Назначение в периоде. `ends_on` — первый день без приёма, `stopped_on` — день отмены."""

    id: UUID
    trade_name: str
    dose_mg: float
    times_per_day: int
    schedule: str
    slot_labels: list[str]
    started_on: date
    ends_on: date | None
    stopped_on: date | None


class DayDoseOut(BaseModel):
    prescription_id: UUID
    slot: int
    status: DoseStatus | None
    marked_at: datetime | None


class AdherenceDayOut(BaseModel):
    day: date
    doses: list[DayDoseOut]


class AdherenceOut(BaseModel):
    """Приём за 14 дней по отметкам пациента. При `course: none` дней и чисел нет."""

    period: PeriodOut | None
    course: CourseState
    prescriptions: list[CourseOut]
    days: list[AdherenceDayOut]
    planned: int
    taken: int
    missed: int
    unmarked: int
    marked_any: bool
