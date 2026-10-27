"""Лимиты приглашений и регистраций (спека #23, разделы 2–3)."""

import httpx
import structlog

from clin_compass.modules.identity.schemas import Role
from clin_compass.modules.patients import service as patients
from tests.conftest import MakeUser
from tests.throttling import ClientFrom, limited_api

PASSWORD = "correct horse battery staple"
IP = "203.0.113.1"
OTHER_IP = "203.0.113.2"


async def logged_in(client_from: ClientFrom, email: str, ip: str = IP) -> httpx.AsyncClient:
    client = client_from(ip)
    response = await client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200
    return client


async def invite(client: httpx.AsyncClient) -> httpx.Response:
    return await client.post("/api/v1/patients/invites")


def registration(token: str, email: str) -> dict[str, object]:
    return {
        "invite_token": token,
        "email": email,
        "password": PASSWORD,
        "full_name": "Петрова Анна Сергеевна",
        "consent_personal_data": True,
        "consent_version": patients.get_consent_document().version,
    }


async def register(client: httpx.AsyncClient, token: str, email: str) -> httpx.Response:
    return await client.post("/api/v1/patients/register", json=registration(token, email))


async def test_doctor_invite_limit_is_per_doctor(postgres_url: str, make_user: MakeUser) -> None:
    await make_user(email="a@example.com")
    await make_user(email="b@example.com")
    async with limited_api(postgres_url, invites_per_doctor=2) as client_from:
        doctor = await logged_in(client_from, "a@example.com")
        assert [(await invite(doctor)).status_code for _ in range(2)] == [201, 201]

        with structlog.testing.capture_logs() as logs:
            blocked = await invite(doctor)
        other_doctor = await invite(await logged_in(client_from, "b@example.com"))

    assert blocked.status_code == 429
    assert blocked.json()["code"] == "rate_limited"
    assert other_doctor.status_code == 201
    assert [e["limits"] for e in logs if e["event"] == "http.rate_limited"] == [("invite.create",)]


async def test_failed_registrations_from_one_ip_are_limited(
    postgres_url: str, make_user: MakeUser
) -> None:
    await make_user(email="a@example.com")
    await make_user(email="taken@example.com", role=Role.PATIENT)
    async with limited_api(postgres_url, registration_failures_per_ip=2) as client_from:
        doctor = await logged_in(client_from, "a@example.com", OTHER_IP)
        token = (await invite(doctor)).json()["token"]
        visitor = client_from(IP)

        invalid = await register(visitor, "no-such-token", "p1@example.com")
        # 409: адрес занят, приглашение остаётся годным — так перебирают адреса.
        taken = await register(visitor, token, "taken@example.com")
        with structlog.testing.capture_logs() as logs:
            blocked = await register(visitor, token, "p2@example.com")
        elsewhere = await register(client_from("198.51.100.9"), token, "p2@example.com")

    assert [invalid.status_code, taken.status_code] == [410, 409]
    assert blocked.status_code == 429
    assert elsewhere.status_code == 201
    limits = [e["limits"] for e in logs if e["event"] == "http.rate_limited"]
    assert limits == [("register.failure",)]


async def test_consent_version_mismatch_does_not_count_as_failed_registration(
    postgres_url: str, make_user: MakeUser
) -> None:
    # Версию сверяют до приглашения и argon2: перебора адресов и нагрузки нет. После смены
    # текста все открытые формы за одним NAT получают 409 и не должны исчерпать лимит.
    await make_user(email="a@example.com")
    async with limited_api(postgres_url, registration_failures_per_ip=1) as client_from:
        doctor = await logged_in(client_from, "a@example.com", OTHER_IP)
        token = (await invite(doctor)).json()["token"]
        visitor = client_from(IP)
        outdated = registration(token, "p1@example.com") | {"consent_version": "2026-10-15-old"}

        mismatches = [
            (await visitor.post("/api/v1/patients/register", json=outdated)).status_code
            for _ in range(2)
        ]
        current = await register(visitor, token, "p1@example.com")

    assert mismatches == [409, 409]
    assert current.status_code == 201
