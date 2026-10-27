"""Пара (scale_id, scale_version) обозначает одно неизменяемое определение шкалы."""

import asyncio

from clin_compass_knowledge.loader import load_knowledge
from clin_compass_knowledge.schemas.scale import Scale
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.assessments.conftest import (
    KNOWLEDGE,
    AssessmentCare,
    MakeAssessmentCare,
    submission,
)

from clin_compass.modules.assessments.schemas import AssessmentCreateIn
from clin_compass.modules.assessments.service import (
    AssessmentError,
    ScaleCatalog,
    create_result,
    definition_snapshot,
)


class _ScaleWithOptionalField(Scale):
    """Схема knowledge после добавления необязательного поля."""

    note: str | None = None


def test_new_optional_schema_field_does_not_change_accepted_definition() -> None:
    original = load_knowledge(KNOWLEDGE).scales["test-eligible"]
    extended = _ScaleWithOptionalField.model_validate(original.model_dump())

    assert definition_snapshot(extended) == definition_snapshot(original)


def test_definition_snapshot_keeps_every_required_field() -> None:
    original = load_knowledge(KNOWLEDGE).scales["test-eligible"]

    assert definition_snapshot(original) == original.model_dump(mode="json")


async def test_concurrent_catalogues_cannot_establish_different_same_version_definitions(
    assessment_app: FastAPI,
    assessment_care: AssessmentCare,
    make_assessment_care: MakeAssessmentCare,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    other = await make_assessment_care(
        "definition-race-doctor@example.com", "definition-race-patient@example.com"
    )
    first_catalog = assessment_app.state.assessment_catalog
    original = first_catalog.scales["test-eligible"]
    changed_item = original.items[0].model_copy(update={"text": "Другой текст вопроса"})
    changed = original.model_copy(update={"items": (changed_item, *original.items[1:])})
    second_catalog = ScaleCatalog(
        scales={**first_catalog.scales, "test-eligible": changed},
        sources=first_catalog.sources,
    )
    barrier = asyncio.Barrier(2)

    async def submit(care: AssessmentCare, catalog: ScaleCatalog) -> str:
        body = AssessmentCreateIn.model_validate(submission(str(care.patient.id)))
        async with sessionmaker() as session:
            await barrier.wait()
            try:
                await create_result(
                    session,
                    catalog,
                    assessment_app.state.alert_rules,
                    patient_id=care.patient.id,
                    body=body,
                )
                await session.commit()
                return "accepted"
            except AssessmentError as error:
                await session.rollback()
                return error.code

    outcomes = await asyncio.gather(
        submit(assessment_care, first_catalog),
        submit(other, second_catalog),
    )

    assert sorted(outcomes) == ["accepted", "scale_definition_conflict"]
