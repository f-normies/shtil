"""Запись результата шкалы: общий путь для самоотчёта пациента и результата врача.

Проверки, подсчёт, снимки, правила сигналов и событие — одной транзакцией (спеки #10, #94).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from clin_compass_knowledge.scoring import ScoreResult, ScoringError, score
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from clin_compass.core import realtime
from clin_compass.modules.alerts import service as alerts_service
from clin_compass.modules.alerts.deps import RuleBook
from clin_compass.modules.assessments.catalog import (
    AssessmentError,
    Respondent,
    ScaleCatalog,
    definition_snapshot,
    raise_if_conflicts,
)
from clin_compass.modules.assessments.models import AssessmentResult
from clin_compass.modules.assessments.schemas import AssessmentOut
from clin_compass.modules.patients import service as patients


@dataclass(frozen=True)
class Submission:
    """Проверенная отправка. `recorded_by` — врач, внёсший результат; None — сам пациент."""

    patient_id: UUID
    submission_id: UUID
    scale_id: str
    scale_version: int
    completed_at: datetime
    answers: Mapping[str, int]
    respondent: Respondent
    recorded_by: UUID | None = None


async def record(
    session: AsyncSession, catalog: ScaleCatalog, rules: RuleBook, sub: Submission
) -> tuple[AssessmentOut, bool]:
    """Проверить, сохранить, обработать и адресовать результат одной транзакцией.

    Возвращает результат и признак новой записи. Коммитит вызывающий.
    """
    patient_id = sub.patient_id
    await _lock_patient(session, patient_id)
    existing = await session.scalar(
        select(AssessmentResult).where(
            AssessmentResult.patient_id == patient_id,
            AssessmentResult.submission_id == sub.submission_id,
        )
    )
    if existing is not None:
        if not _same_submission(existing, sub):
            raise AssessmentError(
                409, "submission_conflict", "Submission key was used for other data"
            )
        return AssessmentOut.model_validate(existing), False

    # Без ведущего врача сработавшее правило некому адресовать: alerts упал бы с 500.
    doctor_id = await patients.lead_doctor_of(session, patient_id)
    if doctor_id is None:
        raise AssessmentError(409, "lead_doctor_missing", "Patient has no lead doctor")

    definition = catalog.get_available(sub.scale_id, sub.respondent)
    if definition is None:
        raise AssessmentError(404, "scale_unavailable", "Scale is unavailable")
    scale = definition.scale
    if sub.scale_version != scale.version:
        raise AssessmentError(
            409,
            "scale_version_changed",
            "Scale version changed",
            current_version=scale.version,
        )

    accepted_definition = await _accepted_definition(session, scale.id, scale.version)
    if accepted_definition is None:
        await _lock_definition(session, scale.id, scale.version)
        accepted_definition = await _accepted_definition(session, scale.id, scale.version)
    raise_if_conflicts(scale, accepted_definition)

    try:
        calculated = score(scale, dict(sub.answers))
    except ScoringError as error:
        raise AssessmentError(422, "invalid_answers", "Answers do not match the scale") from error

    result = AssessmentResult(
        patient_id=patient_id,
        submission_id=sub.submission_id,
        scale_id=scale.id,
        scale_version=scale.version,
        scale_title=scale.title,
        completed_at=sub.completed_at.astimezone(UTC),
        answers=dict(sub.answers),
        scores=_score_snapshot(calculated, catalog),
        definition_snapshot=definition_snapshot(scale),
        sources_snapshot=[source.model_dump(mode="json") for source in definition.sources],
        recorded_by=sub.recorded_by,
    )
    session.add(result)
    await session.flush()

    latest = await _latest_results(session, patient_id)
    await alerts_service.check_results(
        session,
        rules,
        patient_id=patient_id,
        submitted_scale=result.scale_id,
        results=_alert_results(latest, catalog),
        origin_ref=str(result.id),
    )
    # Результат врача пациенту не показываем (#94): событие получает только врач.
    audience = [doctor_id] if sub.recorded_by is not None else [patient_id, doctor_id]
    await realtime.publish(
        session, audience, "assessment.completed", {"assessment_id": str(result.id)}
    )
    return AssessmentOut.model_validate(result), True


async def _lock_patient(session: AsyncSession, patient_id: UUID) -> None:
    """Выстроить отправки одного пациента в очередь во всех экземплярах API."""
    lock_key = f"assessments:patient:{patient_id}"
    await session.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(lock_key, 0))))


async def _lock_definition(session: AsyncSession, scale_id: str, scale_version: int) -> None:
    """Выстроить в очередь первые отправки пары шкалы и версии: базовый снимок один."""
    lock_key = f"assessments:definition:{scale_id}:{scale_version}"
    await session.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(lock_key, 0))))


async def _accepted_definition(
    session: AsyncSession, scale_id: str, scale_version: int
) -> dict[str, Any] | None:
    """Базовый снимок пары: определение первого принятого результата среди всех пациентов."""
    return await session.scalar(
        select(AssessmentResult.definition_snapshot)
        .where(
            AssessmentResult.scale_id == scale_id,
            AssessmentResult.scale_version == scale_version,
        )
        .order_by(AssessmentResult.id.asc())
        .limit(1)
    )


def _same_submission(result: AssessmentResult, sub: Submission) -> bool:
    same = (
        result.scale_id == sub.scale_id
        and result.scale_version == sub.scale_version
        and result.answers == dict(sub.answers)
        and result.recorded_by == sub.recorded_by
    )
    # Время результата врача ставит сервер: повтор придёт позже, время не сравниваем.
    if sub.recorded_by is not None:
        return same
    return same and result.completed_at.astimezone(UTC) == sub.completed_at.astimezone(UTC)


def _score_snapshot(calculated: Mapping[str, ScoreResult], catalog: ScaleCatalog) -> dict[str, Any]:
    snapshots: dict[str, Any] = {}
    for score_id, score_result in calculated.items():
        source_ref = score_result.band.source
        source = catalog.sources[source_ref.source]
        snapshots[score_id] = {
            "value": score_result.value,
            "band": {
                "code": score_result.band.code,
                "label": score_result.band.label,
                "source": {
                    "id": source.id,
                    "locator": source_ref.locator,
                    "title": source.title,
                    "url": str(source.url),
                    "supplementary": source.supplementary,
                },
            },
        }
    return snapshots


async def _latest_results(session: AsyncSession, patient_id: UUID) -> list[AssessmentResult]:
    ranked = (
        select(
            AssessmentResult.id.label("result_id"),
            func.row_number()
            .over(
                partition_by=AssessmentResult.scale_id,
                order_by=AssessmentResult.id.desc(),
            )
            .label("position"),
        )
        .where(AssessmentResult.patient_id == patient_id)
        .subquery()
    )
    statement = (
        select(AssessmentResult)
        .join(ranked, ranked.c.result_id == AssessmentResult.id)
        .where(ranked.c.position == 1)
    )
    return list((await session.scalars(statement)).all())


def _alert_results(
    latest: list[AssessmentResult], catalog: ScaleCatalog
) -> dict[str, dict[str, float]]:
    """Последние результаты по шкалам пациента и врача для правил сигналов (#94)."""
    results: dict[str, dict[str, float]] = {}
    for result in latest:
        current = catalog.get_available(result.scale_id, None)
        if (
            current is None
            or current.scale.version != result.scale_version
            or result.definition_snapshot != definition_snapshot(current.scale)
        ):
            continue
        values = {item_id: float(value) for item_id, value in result.answers.items()}
        values.update(
            {score_id: float(score_data["value"]) for score_id, score_data in result.scores.items()}
        )
        results[result.scale_id] = values
    return results
