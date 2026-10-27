"""Приглашение и регистрация пациента.

ТЗ: связь «врач — пациент» без третьих лиц. 152-ФЗ: согласие на обработку данных.
"""

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.modules.identity.schemas import Role
from clin_compass.modules.patients import service as patients
from clin_compass.modules.patients.schemas import ConsentDocument
from tests.conftest import MakeUser

PASSWORD = "correct horse battery staple"
CURRENT_CONSENT_VERSION = patients.get_consent_document().version


async def login(client: httpx.AsyncClient, email: str) -> None:
    response = await client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200


async def create_invite(client: httpx.AsyncClient) -> str:
    response = await client.post("/api/v1/patients/invites")
    assert response.status_code == 201
    token: str = response.json()["token"]
    return token


def registration(token: str, email: str = "patient@example.com") -> dict[str, object]:
    return {
        "invite_token": token,
        "email": email,
        "password": PASSWORD,
        "full_name": "Петрова Анна Сергеевна",
        "consent_personal_data": True,
        "consent_version": CURRENT_CONSENT_VERSION,
    }


async def test_doctor_creates_invite_with_expiry(
    client: httpx.AsyncClient, make_user: MakeUser
) -> None:
    await make_user(email="doctor@example.com")
    await login(client, "doctor@example.com")

    response = await client.post("/api/v1/patients/invites")

    assert response.status_code == 201
    body = response.json()
    assert len(body["token"]) >= 32
    expires_at = datetime.fromisoformat(body["expires_at"])
    assert expires_at > datetime.now(UTC) + timedelta(days=6)


async def test_patient_cannot_create_invite(client: httpx.AsyncClient, make_user: MakeUser) -> None:
    await make_user(email="patient@example.com", role=Role.PATIENT)
    await login(client, "patient@example.com")

    response = await client.post("/api/v1/patients/invites")

    assert response.status_code == 403


async def test_registration_by_invite_links_patient_to_inviting_doctor(
    client: httpx.AsyncClient, make_user: MakeUser
) -> None:
    await make_user(email="doctor@example.com")
    await login(client, "doctor@example.com")
    token = await create_invite(client)
    client.cookies.clear()

    registered = await client.post("/api/v1/patients/register", json=registration(token))
    me = await client.get("/api/v1/auth/me")
    client.cookies.clear()
    await login(client, "doctor@example.com")
    patients = await client.get("/api/v1/patients")

    assert registered.status_code == 201
    assert registered.json()["role"] == "patient"
    # Пациент сразу вошёл в систему.
    assert me.status_code == 200
    assert me.json()["email"] == "patient@example.com"
    assert [p["email"] for p in patients.json()] == ["patient@example.com"]


async def test_registration_records_personal_data_consent(
    client: httpx.AsyncClient,
    make_user: MakeUser,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await make_user(email="doctor@example.com")
    await login(client, "doctor@example.com")
    token = await create_invite(client)
    client.cookies.clear()

    registered = await client.post("/api/v1/patients/register", json=registration(token))

    async with sessionmaker() as session:
        consents = (
            await session.execute(
                text("SELECT kind, version FROM patients.consents WHERE patient_id = :id"),
                {"id": registered.json()["id"]},
            )
        ).all()
        assert consents == [("personal_data", CURRENT_CONSENT_VERSION)]


async def test_registration_without_consent_is_rejected(
    client: httpx.AsyncClient, make_user: MakeUser
) -> None:
    await make_user(email="doctor@example.com")
    await login(client, "doctor@example.com")
    token = await create_invite(client)
    client.cookies.clear()
    body = registration(token) | {"consent_personal_data": False}

    response = await client.post("/api/v1/patients/register", json=body)

    assert response.status_code == 422


async def test_invite_cannot_be_used_twice(client: httpx.AsyncClient, make_user: MakeUser) -> None:
    await make_user(email="doctor@example.com")
    await login(client, "doctor@example.com")
    token = await create_invite(client)
    client.cookies.clear()

    first = await client.post("/api/v1/patients/register", json=registration(token))
    second = await client.post(
        "/api/v1/patients/register", json=registration(token, email="other@example.com")
    )

    assert first.status_code == 201
    assert second.status_code == 410


async def test_expired_invite_is_rejected(
    client: httpx.AsyncClient,
    make_user: MakeUser,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await make_user(email="doctor@example.com")
    await login(client, "doctor@example.com")
    token = await create_invite(client)
    client.cookies.clear()
    async with sessionmaker() as session:
        await session.execute(
            text("UPDATE patients.invites SET expires_at = now() - interval '1s'")
        )
        await session.commit()

    response = await client.post("/api/v1/patients/register", json=registration(token))

    assert response.status_code == 410


async def test_unknown_invite_is_rejected(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/v1/patients/register", json=registration("no-such-token"))

    assert response.status_code == 410


async def test_current_consent_document_is_public(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/patients/consent")

    assert response.status_code == 200
    assert response.json() == {
        "version": "2026-10-16-draft",
        "text": (
            "Даю согласие на обработку моих персональных данных, включая сведения о состоянии "
            "здоровья, лечащим врачом в целях наблюдения и лечения."
        ),
    }


@pytest.mark.parametrize("version", ["", "x" * 33], ids=["empty", "too-long"])
def test_consent_document_version_fits_registration_request(version: str) -> None:
    # Пациент присылает версию в `consent_version` (1–32 символа, колонка `String(32)`).
    # Документ с версией, которую нельзя принять, должен падать при импорте.
    with pytest.raises(ValidationError):
        ConsentDocument(version=version, text="Текст согласия.")


@pytest.mark.parametrize(
    "version",
    [None, "", "x" * 33],
    ids=["missing", "empty", "too-long"],
)
async def test_registration_rejects_missing_or_invalid_consent_version(
    client: httpx.AsyncClient, make_user: MakeUser, version: str | None
) -> None:
    await make_user(email="doctor@example.com")
    await login(client, "doctor@example.com")
    token = await create_invite(client)
    client.cookies.clear()
    body = registration(token)
    if version is None:
        body.pop("consent_version")
    else:
        body["consent_version"] = version

    response = await client.post("/api/v1/patients/register", json=body)

    assert response.status_code == 422


async def test_outdated_consent_keeps_invite_available_for_current_version(
    client: httpx.AsyncClient, make_user: MakeUser, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    await make_user(email="doctor@example.com")
    await login(client, "doctor@example.com")
    token = await create_invite(client)
    client.cookies.clear()

    for version in ("2026-10-15-old", "unknown-version"):
        outdated = await client.post(
            "/api/v1/patients/register",
            json=registration(token) | {"consent_version": version},
        )
        assert outdated.status_code == 409
        assert outdated.headers["content-type"].startswith("application/problem+json")
        assert outdated.json()["code"] == "consent_version_mismatch"

    current = await client.post("/api/v1/patients/register", json=registration(token))
    assert current.status_code == 201
    async with sessionmaker() as session:
        versions = (
            await session.execute(
                text("SELECT version FROM patients.consents WHERE patient_id = :id"),
                {"id": current.json()["id"]},
            )
        ).scalars()
        assert list(versions) == [CURRENT_CONSENT_VERSION]


async def test_direct_registration_rejects_outdated_consent_before_using_invite(
    make_user: MakeUser, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    doctor = await make_user(email="doctor@example.com")
    async with sessionmaker() as session:
        invite = await patients.create_invite(session, doctor.id, timedelta(days=1))
        registration_args = {
            "invite_token": invite.token,
            "email": "patient@example.com",
            "password": PASSWORD,
            "full_name": "Петрова Анна Сергеевна",
        }

        with pytest.raises(patients.ConsentVersionMismatchError):
            await patients.register_patient(
                session, **registration_args, consent_version="2026-10-15-old"
            )

        patient = await patients.register_patient(
            session, **registration_args, consent_version=CURRENT_CONSENT_VERSION
        )
        await session.commit()

    assert patient.email == "patient@example.com"


async def test_registration_time_can_be_set_for_synthetic_history(
    make_user: MakeUser, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    # Сид (#19) ставит дату начала наблюдения раньше первой записи шкалы. Время приглашения
    # и согласия остаётся настоящим: это записи журнала.
    doctor = await make_user(email="doctor@example.com")
    registered_at = datetime(2026, 9, 1, 7, 0, tzinfo=UTC)
    async with sessionmaker() as session:
        first = await patients.create_invite(session, doctor.id, timedelta(days=1))
        second = await patients.create_invite(session, doctor.id, timedelta(days=1))
        backdated = await patients.register_patient(
            session,
            invite_token=first.token,
            email="old@example.com",
            password=PASSWORD,
            full_name="Петрова Анна Сергеевна",
            consent_version=CURRENT_CONSENT_VERSION,
            registered_at=registered_at,
        )
        current = await patients.register_patient(
            session,
            invite_token=second.token,
            email="new@example.com",
            password=PASSWORD,
            full_name="Петров Иван Сергеевич",
            consent_version=CURRENT_CONSENT_VERSION,
        )
        await session.commit()
        listed = {p.id: p.created_at for p in await patients.list_patients(session, doctor.id)}
        journal = (
            await session.execute(
                text(
                    "SELECT min(i.used_at), min(c.given_at) FROM patients.invites i, "
                    "patients.consents c"
                )
            )
        ).one()

    assert listed[backdated.id] == registered_at
    assert abs(listed[current.id] - datetime.now(UTC)) < timedelta(minutes=1)
    assert all(t > registered_at for t in journal)
