"""Повторно используемые HTTP-схемы отправки результата."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from clin_compass.modules.assessments.schemas import (
    AssessmentCreatedOut,
    AssessmentCreateIn,
    AssessmentOut,
)


def test_assessment_request_rejects_boolean_answer_values() -> None:
    request = {
        "patient_id": "00000000-0000-0000-0000-000000000001",
        "submission_id": "00000000-0000-0000-0000-000000000002",
        "scale_id": "test-eligible",
        "scale_version": 1,
        "completed_at": "2026-10-17T14:00:00Z",
        "answers": {"q1": True},
    }

    with pytest.raises(ValidationError):
        AssessmentCreateIn.model_validate(request)


def test_assessment_request_requires_timezone_aware_completion_time() -> None:
    request = {
        "patient_id": "00000000-0000-0000-0000-000000000001",
        "submission_id": "00000000-0000-0000-0000-000000000002",
        "scale_id": "test-eligible",
        "scale_version": 1,
        "completed_at": "2026-10-17T14:00:00",
        "answers": {"q1": 1},
    }

    with pytest.raises(ValidationError):
        AssessmentCreateIn.model_validate(request)


def test_created_response_keeps_the_result_and_adds_the_alert_flag() -> None:
    # Спека #57, раздел 7.2: ответ на отправку — результат и признак сигнала.
    now = datetime.now(UTC)
    result = AssessmentOut.model_validate(
        {
            "id": uuid4(),
            "submission_id": uuid4(),
            "patient_id": uuid4(),
            "scale_id": "phq-9",
            "scale_version": 1,
            "scale_title": "PHQ-9",
            "completed_at": now,
            "received_at": now,
            "answers": {"q9": 1},
            "scores": {},
            "sources_snapshot": [
                {
                    "id": "s",
                    "title": "Источник",
                    "url": "https://example.org",
                    "supplementary": False,
                }
            ],
        }
    )

    created = AssessmentCreatedOut.of(result, alert_raised=True)

    assert created.alert_raised is True
    assert created.model_dump(exclude={"alert_raised"}) == result.model_dump()
