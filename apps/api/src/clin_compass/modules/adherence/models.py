"""Таблицы модуля adherence (схема Postgres `adherence`)."""

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, Date, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from clin_compass.core.orm import Base, created_at, uuid_pk
from clin_compass.modules.adherence.doses import MarkStatus

SCHEMA = "adherence"


class DoseMark(Base):
    """Отметка пациента о дозе. Строки не меняются и не удаляются: действует последняя по дозе.

    `status`: `taken` — принята, `missed` — пропущена, `cleared` — отметка снята.
    """

    __tablename__ = "dose_marks"
    __table_args__ = (
        CheckConstraint("status IN ('taken', 'missed', 'cleared')", name="status"),
        CheckConstraint("slot >= 0", name="slot"),
        Index("ix_adherence_dose_marks_dose", "prescription_id", "day", "slot", "marked_at"),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = uuid_pk()
    patient_id: Mapped[UUID] = mapped_column(ForeignKey("identity.users.id"), index=True)
    prescription_id: Mapped[UUID] = mapped_column(ForeignKey("therapy.prescriptions.id"))
    day: Mapped[date] = mapped_column(Date)
    slot: Mapped[int] = mapped_column(Integer)
    status: Mapped[MarkStatus] = mapped_column(String(16))
    marked_at: Mapped[datetime] = created_at()
