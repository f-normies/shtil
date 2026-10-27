"""`GET /api/v1/patient-help` на настоящей БД (спека #57, раздел 7.1).

Экран и правила — выдуманные, из tests/alerts/knowledge.
"""

import httpx

from tests.alerts.conftest import Care
from tests.alerts.test_api import login

URL = "/api/v1/patient-help"


async def test_patient_reads_help_with_alert_rule_triggers(
    client: httpx.AsyncClient, care: Care
) -> None:
    await login(client, "patient@example.com")

    response = await client.get(URL)

    assert response.status_code == 200
    body = response.json()
    assert [(c["number"], c["primary"]) for c in body["contacts"]] == [
        ("112", True),
        ("103", False),
    ]
    assert [t["rule_id"] for t in body["triggers"]] == ["test-self-harm"]


async def test_doctor_is_forbidden(client: httpx.AsyncClient, care: Care) -> None:
    await login(client, "doctor@example.com")

    assert (await client.get(URL)).status_code == 403


async def test_anonymous_is_unauthorized(client: httpx.AsyncClient) -> None:
    assert (await client.get(URL)).status_code == 401
