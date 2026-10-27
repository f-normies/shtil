"""Публичные схемы других модулей для табличных тестов фактов: результат шкалы, сигнал, находка."""

from datetime import date, datetime
from typing import Any
from uuid import UUID, uuid4

from clin_compass.modules.alerts.schemas import AlertOut
from clin_compass.modules.assessments.schemas import AssessmentOut
from clin_compass.modules.freetext.schemas import FindingOut

PATIENT = uuid4()
DOCTOR = uuid4()
PHQ9 = "Опросник здоровья пациента (PHQ-9)"
_BAND_SOURCE = {
    "id": "zolotareva-gad7-2023",
    "locator": "с. 35",
    "title": "Источник порогов",
    "url": "https://example.org/source",
    "supplementary": False,
}


def result(
    completed: datetime,
    value: int,
    band: str,
    *,
    received: datetime | None = None,
    scale_id: str = "phq-9",
    title: str = PHQ9,
    scores: dict[str, tuple[int, str]] | None = None,
) -> AssessmentOut:
    """Результат шкалы: сумма `total` или суммы `scores` (id → балл и градация)."""
    sums = scores if scores is not None else {"total": (value, band)}
    return AssessmentOut.model_validate(
        {
            "id": uuid4(),
            "submission_id": uuid4(),
            "patient_id": PATIENT,
            "scale_id": scale_id,
            "scale_version": 1,
            "scale_title": title,
            "completed_at": completed,
            "received_at": received or completed,
            "answers": {},
            "scores": {
                key: {
                    "value": points,
                    "band": {"code": "band", "label": label, "source": _BAND_SOURCE},
                }
                for key, (points, label) in sums.items()
            },
            "sources_snapshot": [],
        }
    )


def alert(
    created: datetime,
    title: str,
    *,
    level: str = "mandatory",
    kind: str = "alert",
    state: str = "new",
    last_triggered: datetime | None = None,
    occurrences: int = 1,
    snoozed_until: date | None = None,
    resolved: datetime | None = None,
    decision: str | None = None,
) -> AlertOut:
    """Сигнал врачу; решение — действие `resolve` с текстом варианта."""
    actions: list[dict[str, Any]] = []
    if resolved is not None:
        actions.append(
            {
                "at": resolved,
                "actor_id": DOCTOR,
                "action": "resolve",
                "decision_id": "decision",
                "decision_text": decision,
                "reason": None,
                "snoozed_until": None,
            }
        )
    return AlertOut.model_validate(
        {
            "id": uuid4(),
            "patient_id": PATIENT,
            "doctor_id": DOCTOR,
            "origin_type": "rule",
            "rule_id": "rule",
            "rule_version": 1,
            "kind": kind,
            "level": level,
            "title": title,
            "doctor_actions": ["Связаться с пациентом"],
            "sources": [],
            "occurrences": occurrences,
            "created_at": created,
            "last_triggered_at": last_triggered or created,
            "delivered_at": None,
            "delivered_via": None,
            "acknowledged_at": None,
            "acknowledged_by": None,
            "state": state,
            "snoozed_until": snoozed_until,
            "resolved_at": resolved,
            "resolved_by": DOCTOR if resolved is not None else None,
            "triggers": [],
            "actions": actions,
        }
    )


def finding(
    confirmed: datetime,
    category: str,
    summary: str,
    quote: str,
    *,
    source_type: str = "diary",
    transcript: bool = False,
    drug: str | None = None,
    event: str | None = None,
    analysis_id: UUID | None = None,
) -> FindingOut:
    """Подтверждённая находка дневника или чата."""
    return FindingOut(
        id=uuid4(),
        proposal_id=uuid4(),
        analysis_id=analysis_id or uuid4(),
        patient_id=PATIENT,
        category=category,  # type: ignore[arg-type]
        data={
            "source_type": source_type,
            "transcript": transcript,
            "category": category,
            "summary": summary,
            "quote": quote,
            "drug": None if drug is None else {"name": drug, "check": "found", "matches": []},
            "adherence_event": event,
        },
        confirmed_at=confirmed,
        confirmed_by=DOCTOR,
    )
