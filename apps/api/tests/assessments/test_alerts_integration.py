"""Связь сохранённого результата с публичным alerts-сервисом и NOTIFY."""

import asyncio
from typing import NoReturn

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.assessments.conftest import (
    BASE,
    AssessmentCare,
    AssessmentNotifications,
    login,
    submission,
)

from clin_compass.modules.alerts import service as alerts_service
from clin_compass.modules.alerts.models import Alert, Trigger
from clin_compass.modules.assessments.models import AssessmentResult
from clin_compass.modules.assessments.service import ScaleCatalog


async def test_concurrent_different_scales_check_real_latest_results_and_notify_privately(
    assessment_client: AsyncClient,
    assessment_care: AssessmentCare,
    assessment_notifications: AssessmentNotifications,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await login(assessment_client, assessment_care.patient.email)
    first_body = submission(
        str(assessment_care.patient.id),
        answers={"q1": 1, "q2": 1},
    )
    second_body = submission(
        str(assessment_care.patient.id),
        scale_id="test-eligible-second",
        answers={"q3": 1},
    )

    first, second = await asyncio.gather(
        assessment_client.post(BASE, json=first_body),
        assessment_client.post(BASE, json=second_body),
    )
    assert first.status_code == second.status_code == 201
    events = await assessment_notifications.wait_for(3)
    completed = [event for event in events if event["type"] == "assessment.completed"]
    alerts = [event for event in events if event["type"] == "alert.created"]
    assert len(completed) == 2
    assert all(set(event["data"]) == {"assessment_id"} for event in completed)
    assert all(
        event["to"] == [str(assessment_care.patient.id), str(assessment_care.doctor.id)]
        for event in completed
    )
    assert len(alerts) == 1
    assert alerts[0]["to"] == [str(assessment_care.doctor.id)]
    assert set(alerts[0]["data"]) == {"alert_id"}
    async with sessionmaker() as session:
        trigger = await session.scalar(select(Trigger))
        alert_count = await session.scalar(select(func.count()).select_from(Alert))
    assert alert_count == 1
    assert trigger is not None
    assert trigger.evidence == {
        "test-eligible": {"total": 2.0},
        "test-eligible-second": {"q3": 1.0},
    }
    assert trigger.origin_ref in {first.json()["id"], second.json()["id"]}
    assert await assessment_notifications.settle() == events


async def test_stale_latest_scale_version_is_excluded_from_cross_scale_alerts(
    assessment_client: AsyncClient,
    assessment_app: FastAPI,
    assessment_care: AssessmentCare,
    assessment_notifications: AssessmentNotifications,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await login(assessment_client, assessment_care.patient.email)
    first = await assessment_client.post(
        BASE,
        json=submission(str(assessment_care.patient.id), answers={"q1": 1, "q2": 1}),
    )
    assert first.status_code == 201

    catalog = assessment_app.state.assessment_catalog
    current = catalog.scales["test-eligible"].model_copy(update={"version": 2})
    assessment_app.state.assessment_catalog = ScaleCatalog(
        scales={**catalog.scales, "test-eligible": current}, sources=catalog.sources
    )

    second = await assessment_client.post(
        BASE,
        json=submission(
            str(assessment_care.patient.id),
            scale_id="test-eligible-second",
            answers={"q3": 1},
        ),
    )

    assert second.status_code == 201
    events = await assessment_notifications.wait_for(2)
    assert [event["type"] for event in events] == [
        "assessment.completed",
        "assessment.completed",
    ]
    assert await assessment_notifications.settle() == events
    async with sessionmaker() as session:
        assert await session.scalar(select(func.count()).select_from(Alert)) == 0


async def test_same_version_changed_definition_is_excluded_from_cross_scale_alerts(
    assessment_client: AsyncClient,
    assessment_app: FastAPI,
    assessment_care: AssessmentCare,
    assessment_notifications: AssessmentNotifications,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await login(assessment_client, assessment_care.patient.email)
    first = await assessment_client.post(
        BASE,
        json=submission(str(assessment_care.patient.id), answers={"q1": 1, "q2": 1}),
    )
    assert first.status_code == 201

    catalog = assessment_app.state.assessment_catalog
    original = catalog.scales["test-eligible"]
    changed_total = original.scores[0].model_copy(update={"items": ("q1",)})
    changed = original.model_copy(update={"scores": (changed_total, *original.scores[1:])})
    assert changed.version == original.version
    assessment_app.state.assessment_catalog = ScaleCatalog(
        scales={**catalog.scales, "test-eligible": changed}, sources=catalog.sources
    )

    second = await assessment_client.post(
        BASE,
        json=submission(
            str(assessment_care.patient.id),
            scale_id="test-eligible-second",
            answers={"q3": 1},
        ),
    )

    assert second.status_code == 201
    events = await assessment_notifications.wait_for(2)
    assert [event["type"] for event in events] == [
        "assessment.completed",
        "assessment.completed",
    ]
    assert await assessment_notifications.settle() == events
    async with sessionmaker() as session:
        assert await session.scalar(select(func.count()).select_from(Alert)) == 0


async def test_alert_failure_rolls_back_result_and_publishes_nothing(
    assessment_app: FastAPI,
    assessment_care: AssessmentCare,
    assessment_notifications: AssessmentNotifications,
    sessionmaker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_check(*_args: object, **_kwargs: object) -> NoReturn:
        raise RuntimeError("synthetic alert failure")

    monkeypatch.setattr(alerts_service, "check_results", fail_check)

    async with AsyncClient(
        transport=ASGITransport(app=assessment_app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        await login(client, assessment_care.patient.email)
        response = await client.post(
            BASE,
            json=submission(str(assessment_care.patient.id)),
        )

    assert response.status_code == 500
    assert await assessment_notifications.settle() == []
    async with sessionmaker() as session:
        result_count = await session.scalar(select(func.count()).select_from(AssessmentResult))
        alert_count = await session.scalar(select(func.count()).select_from(Alert))
    assert result_count == alert_count == 0
