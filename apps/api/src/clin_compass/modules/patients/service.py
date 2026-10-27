"""Публичный интерфейс модуля patients: приглашения, регистрация, пациенты врача,
временный доступ заместителя (`access.py`, спека #56)."""

from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from clin_compass.core import tokens
from clin_compass.modules.identity import service as identity
from clin_compass.modules.identity.schemas import DisabledReason, Role, UserOut
from clin_compass.modules.patients import access
from clin_compass.modules.patients.access import (
    end_access,
    end_deputy_access,
    grant_access,
    granted_alerts,
    grants_of,
)
from clin_compass.modules.patients.models import Consent, Diagnosis, Invite, Patient
from clin_compass.modules.patients.preferences import (
    PatientNotFoundError,
    PreferencesRevisionConflictError,
    complete_preferences,
    get_preferences,
    update_preferences,
)
from clin_compass.modules.patients.schemas import (
    AcceptedConsentOut,
    ChildPugh,
    ConsentDocument,
    DiagnosisIn,
    DiagnosisOut,
    InviteOut,
    PatientSummary,
    ProfileUpdate,
    Sex,
)

__all__ = [
    "PatientNotFoundError",
    "PreferencesRevisionConflictError",
    "complete_preferences",
    "end_access",
    "end_deputy_access",
    "get_preferences",
    "grant_access",
    "granted_alerts",
    "grants_of",
    "update_preferences",
]

_CONSENT_DOCUMENT = ConsentDocument(
    version="2026-10-16-draft",
    text=(
        "Даю согласие на обработку моих персональных данных, включая сведения о состоянии "
        "здоровья, лечащим врачом в целях наблюдения и лечения."
    ),
)


class InviteUnavailableError(Exception):
    """Приглашение не найдено, уже использовано или истекло."""


class ConsentVersionMismatchError(Exception):
    """Пациент принял версию согласия, которая уже не действует."""


class DiagnosisNotFoundError(Exception):
    """Диагноза нет у этого пациента."""


class AcceptedConsentNotFoundError(Exception):
    """У пациента нет принятого согласия на обработку данных."""


def get_consent_document() -> ConsentDocument:
    """Вернуть единственную текущую версию текста согласия."""
    return _CONSENT_DOCUMENT


async def get_accepted_consent(session: AsyncSession, patient_id: UUID) -> AcceptedConsentOut:
    """Вернуть последнюю принятую версию и только текст именно этой версии."""
    if await session.get(Patient, patient_id) is None:
        raise PatientNotFoundError
    consent = await session.scalar(
        select(Consent)
        .where(Consent.patient_id == patient_id, Consent.kind == "personal_data")
        .order_by(Consent.given_at.desc(), Consent.id.desc())
        .limit(1)
    )
    if consent is None:
        raise AcceptedConsentNotFoundError
    document = get_consent_document()
    return AcceptedConsentOut(
        version=consent.version,
        given_at=consent.given_at,
        text=document.text if consent.version == document.version else None,
    )


async def create_invite(session: AsyncSession, doctor_id: UUID, ttl: timedelta) -> InviteOut:
    token = tokens.new_token()
    invite = Invite(
        token_hash=tokens.token_hash(token),
        doctor_id=doctor_id,
        expires_at=datetime.now(UTC) + ttl,
    )
    session.add(invite)
    await session.flush()
    return InviteOut(token=token, expires_at=invite.expires_at)


async def register_patient(
    session: AsyncSession,
    *,
    invite_token: str,
    email: str,
    password: str,
    full_name: str,
    consent_version: str,
    registered_at: datetime | None = None,
) -> UserOut:
    """Регистрирует пациента по приглашению и привязывает к пригласившему врачу.

    Согласие на обработку персональных данных записывается в той же транзакции.
    `registered_at` — дата начала наблюдения в истории синтетического пациента (`make seed`).
    API его не передаёт: по умолчанию время ставит БД.
    """
    if consent_version != get_consent_document().version:
        raise ConsentVersionMismatchError

    invite = await session.scalar(
        select(Invite).where(Invite.token_hash == tokens.token_hash(invite_token)).with_for_update()
    )
    now = datetime.now(UTC)
    if invite is None or invite.used_at is not None or invite.expires_at <= now:
        raise InviteUnavailableError

    user = await identity.create_user(
        session, email=email, password=password, role=Role.PATIENT, full_name=full_name
    )
    patient = Patient(user_id=user.id, lead_doctor_id=invite.doctor_id)
    if registered_at is not None:
        patient.created_at = registered_at
    session.add(patient)
    await session.flush()
    session.add(Consent(patient_id=user.id, kind="personal_data", version=consent_version))
    invite.used_at = now
    invite.used_by = user.id
    await session.flush()
    return user


async def revoke_consent(session: AsyncSession, patient_id: UUID) -> None:
    """Пациент отзывает согласие (спека #112, 3.3). Коммитит вызывающий — одной транзакцией.

    Собранные данные остаются у врача (3.1). Канал пациента закрывается: вход, все сессии
    и их сокеты. Роль проверяет вызывающий.
    """
    await session.execute(
        update(Consent)
        .where(Consent.patient_id == patient_id, Consent.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )
    await identity.disable_user(session, patient_id, DisabledReason.CONSENT_REVOKED)
    await identity.revoke_user_sessions(session, patient_id)
    await identity.log_access(
        session, actor_id=patient_id, action="consent.revoke", patient_id=patient_id
    )


async def list_patients(session: AsyncSession, doctor_id: UUID) -> list[PatientSummary]:
    patients = (
        await session.scalars(
            select(Patient).where(Patient.lead_doctor_id == doctor_id).order_by(Patient.created_at)
        )
    ).all()
    ids = [p.user_id for p in patients]
    users = {u.id: u for u in await identity.get_users(session, ids)}
    revoked = await _consent_revoked_at(session, ids)
    return [_summary(p, users[p.user_id], revoked.get(p.user_id)) for p in patients]


async def get_patient_for_doctor(
    session: AsyncSession, *, doctor_id: UUID, patient_id: UUID, deputy: bool = False
) -> PatientSummary | None:
    """Пациент, если запрашивающий — его ведущий врач. Иначе None (существование не раскрываем).

    `deputy` — пустить и заместителя с действующим доступом по сигналу (спека #56): профиль
    тогда урезан до минимального набора, `access = deputy`.
    """
    patient = await session.get(Patient, patient_id)
    if patient is None:
        return None
    if patient.lead_doctor_id != doctor_id:
        if not deputy or not await access.has_access(
            session, doctor_id=doctor_id, patient_id=patient_id
        ):
            return None
        user = await identity.get_user(session, patient.user_id)
        return _deputy_summary(patient, user) if user is not None else None
    user = await identity.get_user(session, patient.user_id)
    if user is None:
        return None
    revoked = await _consent_revoked_at(session, [patient.user_id])
    return _summary(patient, user, revoked.get(patient.user_id))


async def set_phone(
    session: AsyncSession, patient: PatientSummary, phone: str | None
) -> PatientSummary:
    """Записать телефон пациента. Владение проверяет вызывающий (deps.owned_patient)."""
    row = await session.get_one(Patient, patient.id)
    row.phone = phone
    await session.flush()
    return patient.model_copy(update={"phone": phone})


async def set_profile(
    session: AsyncSession, patient: PatientSummary, profile: ProfileUpdate
) -> PatientSummary:
    """Клинический профиль от врача. Владение проверяет вызывающий (deps.owned_patient).

    Поле, которого нет в запросе, не меняется: сид шлёт только дату рождения и пол, и СКФ
    не должна стираться. Явный `null` стирает значение.
    """
    row = await session.get_one(Patient, patient.id)
    fields = profile.model_dump(exclude_unset=True)
    for name, value in fields.items():
        setattr(row, name, value)
    await session.flush()
    return patient.model_copy(update=fields)


async def list_diagnoses(session: AsyncSession, patient_id: UUID) -> list[DiagnosisOut]:
    """Все диагнозы пациента, снятые тоже, в порядке постановки."""
    rows = await session.scalars(
        select(Diagnosis).where(Diagnosis.patient_id == patient_id).order_by(Diagnosis.set_at)
    )
    return [DiagnosisOut.model_validate(d) for d in rows]


async def add_diagnosis(
    session: AsyncSession, patient_id: UUID, diagnosis: DiagnosisIn, *, doctor_id: UUID
) -> DiagnosisOut:
    row = Diagnosis(
        patient_id=patient_id, icd10=diagnosis.icd10, title=diagnosis.title, set_by=doctor_id
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)
    return DiagnosisOut.model_validate(row)


async def resolve_diagnosis(
    session: AsyncSession, patient_id: UUID, diagnosis_id: UUID, *, doctor_id: UUID
) -> DiagnosisOut:
    """Снять диагноз. Строка остаётся: медицинские данные физически не удаляем."""
    row = await session.scalar(
        select(Diagnosis)
        .where(Diagnosis.id == diagnosis_id, Diagnosis.patient_id == patient_id)
        .with_for_update()
    )
    if row is None:
        raise DiagnosisNotFoundError
    if row.resolved_at is None:
        row.resolved_at = datetime.now(UTC)
        row.resolved_by = doctor_id
        await session.flush()
    return DiagnosisOut.model_validate(row)


async def registered_at(session: AsyncSession, patient_id: UUID) -> datetime | None:
    """Момент регистрации пациента: от него идёт расписание шкал (спека #111)."""
    return await session.scalar(select(Patient.created_at).where(Patient.user_id == patient_id))


async def lead_doctor_of(session: AsyncSession, patient_id: UUID) -> UUID | None:
    patient = await session.get(Patient, patient_id)
    return patient.lead_doctor_id if patient is not None else None


async def observation_start(session: AsyncSession, patient_id: UUID) -> datetime:
    """Начало наблюдения пациента для проверки дневникового диапазона."""
    patient = await session.get(Patient, patient_id)
    if patient is None:
        raise ValueError("patient does not exist")
    return patient.created_at


async def lead_doctor_of_for_update(session: AsyncSession, patient_id: UUID) -> UUID | None:
    """Прочитать ведущего врача под блокировкой строки пациента до коммита.

    Messaging использует это чтение перед записью, чтобы смена ведущего врача и
    отправка сообщения не прошли одновременно в разных текущих парах.
    """
    # Без populate_existing ORM вернёт пациента, уже загруженного в сессию, со старым
    # врачом, хотя строка заблокирована после смены врача в другой транзакции.
    patient = await session.scalar(
        select(Patient)
        .where(Patient.user_id == patient_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return patient.lead_doctor_id if patient is not None else None


async def _consent_revoked_at(
    session: AsyncSession, patient_ids: list[UUID]
) -> dict[UUID, datetime]:
    """Когда пациенты отозвали согласие; кто не отзывал, в словаре нет."""
    if not patient_ids:
        return {}
    rows = await session.execute(
        select(Consent.patient_id, func.max(Consent.revoked_at))
        .where(Consent.patient_id.in_(patient_ids))
        .group_by(Consent.patient_id)
    )
    return {patient_id: revoked_at for patient_id, revoked_at in rows if revoked_at is not None}


def _deputy_summary(patient: Patient, user: UserOut) -> PatientSummary:
    """Минимальный набор для заместителя (спека #56, 9.7): без почты и клинического профиля."""
    return PatientSummary(
        id=patient.user_id,
        full_name=user.full_name,
        email="",
        lead_doctor_id=patient.lead_doctor_id,
        created_at=patient.created_at,
        phone=patient.phone,
        birth_date=patient.birth_date,
        sex=cast("Sex | None", patient.sex),
        access="deputy",
    )


def _summary(
    patient: Patient, user: UserOut, consent_revoked_at: datetime | None = None
) -> PatientSummary:
    return PatientSummary(
        id=patient.user_id,
        full_name=user.full_name,
        email=user.email,
        lead_doctor_id=patient.lead_doctor_id,
        created_at=patient.created_at,
        phone=patient.phone,
        birth_date=patient.birth_date,
        sex=cast("Sex | None", patient.sex),
        pregnant=patient.pregnant,
        breastfeeding=patient.breastfeeding,
        egfr=patient.egfr,
        egfr_measured_on=patient.egfr_measured_on,
        child_pugh=cast("ChildPugh | None", patient.child_pugh),
        consent_revoked_at=consent_revoked_at,
    )
