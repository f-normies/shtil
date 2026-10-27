"""Клинический сценарий: `scenarios/<id>.yaml` (спека, 4.5)."""

from typing import Literal

from pydantic import Field

from clin_compass_knowledge.schemas.common import Id, Model, NonEmpty, SourceRef, Status


class Patient(Model):
    """Синтетический профиль пациента."""

    sex: Literal["female", "male"]
    age: int = Field(ge=0, le=120)
    summary: NonEmpty
    history: NonEmpty


class Refs(Model):
    """Шкалы и правила, которые задействует шаг."""

    scales: tuple[Id, ...] = ()
    rules: tuple[Id, ...] = ()


class Step(Model):
    """Шаг сценария и ожидаемое поведение системы."""

    id: Id
    actor: Literal["doctor", "patient", "system"]
    action: NonEmpty
    expected: NonEmpty
    refs: Refs = Refs()
    sources: tuple[SourceRef, ...] = ()


class Scenario(Model):
    """Сценарий для демо, презентации и сквозных тестов."""

    id: Id
    status: Status
    title: NonEmpty
    patient: Patient
    steps: tuple[Step, ...] = Field(min_length=1)
