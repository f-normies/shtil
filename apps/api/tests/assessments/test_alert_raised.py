"""Признак `alert_raised` в ответе на отправку шкалы (спека #57, раздел 7.2).

Правило `test-cross-scale` (`kind: alert`) срабатывает, когда сумма `test-eligible` ≥ 2
и пункт q3 `test-eligible-second` ≥ 1.
"""

from httpx import AsyncClient
from tests.assessments.conftest import BASE, AssessmentCare, login, submission


async def test_submission_without_firing_has_no_alert(
    assessment_client: AsyncClient, assessment_care: AssessmentCare
) -> None:
    await login(assessment_client, assessment_care.patient.email)

    response = await assessment_client.post(
        BASE, json=submission(str(assessment_care.patient.id), answers={"q1": 1, "q2": 1})
    )

    assert response.status_code == 201
    assert response.json()["alert_raised"] is False


async def test_firing_submission_and_its_replay_report_the_alert(
    assessment_client: AsyncClient, assessment_care: AssessmentCare
) -> None:
    patient_id = str(assessment_care.patient.id)
    await login(assessment_client, assessment_care.patient.email)
    await assessment_client.post(BASE, json=submission(patient_id, answers={"q1": 1, "q2": 1}))
    firing = submission(patient_id, scale_id="test-eligible-second", answers={"q3": 1})

    created = await assessment_client.post(BASE, json=firing)
    # Первый ответ потерялся в сети: PWA повторяет ту же отправку.
    replayed = await assessment_client.post(BASE, json=firing)

    assert (created.status_code, created.json()["alert_raised"]) == (201, True)
    assert (replayed.status_code, replayed.json()["alert_raised"]) == (200, True)


async def test_history_has_no_alert_flag(
    assessment_client: AsyncClient, assessment_care: AssessmentCare
) -> None:
    # Признак — только в ответе на отправку: история и результат врача не меняются.
    await login(assessment_client, assessment_care.patient.email)
    await assessment_client.post(BASE, json=submission(str(assessment_care.patient.id)))

    history = await assessment_client.get(BASE)

    assert "alert_raised" not in history.json()["items"][0]
