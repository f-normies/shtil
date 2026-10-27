"""Фикстуры сводки: врач вошёл в API, у него пациент без данных."""

import httpx
import pytest

from tests.conftest import MakeUser
from tests.patients.test_access import login, register_patient
from tests.summary.helpers import SummaryCare

DOCTOR = "doctor@example.com"


@pytest.fixture
async def care(client: httpx.AsyncClient, make_user: MakeUser) -> SummaryCare:
    doctor = await make_user(email=DOCTOR)
    patient_id = await register_patient(client, DOCTOR, "patient@example.com")
    await login(client, DOCTOR)
    return SummaryCare(client, doctor, patient_id)
