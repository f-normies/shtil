"""HTTP-роутер модуля patients."""

from datetime import timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

from clin_compass.core.deps import AppSettings, DbSession
from clin_compass.core.errors import PROBLEM_JSON, RATE_LIMITED_RESPONSE, ProblemOut, problem
from clin_compass.modules.identity import service as identity
from clin_compass.modules.identity.deps import (
    SESSION_COOKIE,
    DoctorUser,
    PatientUser,
    set_session_cookie,
)
from clin_compass.modules.identity.schemas import UserOut
from clin_compass.modules.patients import service
from clin_compass.modules.patients.deps import owned_patient
from clin_compass.modules.patients.schemas import (
    AcceptedConsentOut,
    ConsentDocument,
    InviteOut,
    PatientPreferencesOut,
    PatientPreferencesUpdate,
    PatientSummary,
    PhoneUpdate,
    RegistrationRequest,
)

router = APIRouter(prefix="/patients", tags=["patients"])
me_router = APIRouter(tags=["patients"])

_PREFERENCES_CONFLICT: dict[int | str, dict[str, Any]] = {
    409: {
        "description": "Preferences revision conflict",
        "content": {PROBLEM_JSON: {"schema": ProblemOut.model_json_schema()}},
    }
}


@me_router.get("/me/preferences", response_model=PatientPreferencesOut)
async def get_my_preferences(
    patient: PatientUser, session: DbSession, settings: AppSettings
) -> PatientPreferencesOut:
    """Прочитать настройки текущего пациента с виртуальными исходными значениями."""
    try:
        result = await service.get_preferences(session, patient, timezone=str(settings.timezone))
    except service.PatientNotFoundError:
        raise HTTPException(status_code=404, detail="Patient not found") from None
    await identity.log_access(
        session, actor_id=patient.id, action="patients.preferences.read", patient_id=patient.id
    )
    await session.commit()
    return result


@me_router.patch(
    "/me/preferences",
    response_model=PatientPreferencesOut,
    responses=_PREFERENCES_CONFLICT,
)
async def patch_my_preferences(
    body: PatientPreferencesUpdate,
    patient: PatientUser,
    session: DbSession,
    settings: AppSettings,
) -> PatientPreferencesOut | JSONResponse:
    """Частично сохранить настройки текущего пациента при совпадении версии."""
    try:
        result = await service.update_preferences(
            session, patient, body, timezone=str(settings.timezone)
        )
    except service.PreferencesRevisionConflictError:
        return problem(
            409,
            "Preferences have changed; read them again before updating",
            code="preferences_revision_conflict",
        )
    except service.PatientNotFoundError:
        raise HTTPException(status_code=404, detail="Patient not found") from None
    await identity.log_access(
        session, actor_id=patient.id, action="patients.preferences.update", patient_id=patient.id
    )
    await session.commit()
    return result


@me_router.post(
    "/me/preferences/complete",
    response_model=PatientPreferencesOut,
    responses=_PREFERENCES_CONFLICT,
)
async def complete_my_preferences(
    body: PatientPreferencesUpdate,
    patient: PatientUser,
    session: DbSession,
    settings: AppSettings,
) -> PatientPreferencesOut | JSONResponse:
    """Сохранить выбор онбординга и отметить его завершённым один раз."""
    try:
        result = await service.complete_preferences(
            session, patient, body, timezone=str(settings.timezone)
        )
    except service.PreferencesRevisionConflictError:
        return problem(
            409,
            "Preferences have changed; read them again before completing onboarding",
            code="preferences_revision_conflict",
        )
    except service.PatientNotFoundError:
        raise HTTPException(status_code=404, detail="Patient not found") from None
    await identity.log_access(
        session,
        actor_id=patient.id,
        action="patients.preferences.complete",
        patient_id=patient.id,
    )
    await session.commit()
    return result


@me_router.get("/me/consent", response_model=AcceptedConsentOut)
async def get_my_accepted_consent(patient: PatientUser, session: DbSession) -> AcceptedConsentOut:
    """Прочитать собственную версию согласия и её текст при наличии точного документа."""
    try:
        result = await service.get_accepted_consent(session, patient.id)
    except service.PatientNotFoundError:
        raise HTTPException(status_code=404, detail="Patient not found") from None
    except service.AcceptedConsentNotFoundError:
        raise HTTPException(status_code=404, detail="Accepted consent not found") from None
    await identity.log_access(
        session, actor_id=patient.id, action="patients.consent.read", patient_id=patient.id
    )
    await session.commit()
    return result


_PROBLEM_CONTENT = {PROBLEM_JSON: {"schema": ProblemOut.model_json_schema()}}


@router.get("/consent", response_model=ConsentDocument)
async def get_consent_document() -> ConsentDocument:
    """Текущий документ согласия доступен до регистрации и без входа."""
    return service.get_consent_document()


@router.post("/invites", status_code=201, responses=RATE_LIMITED_RESPONSE)
async def create_invite(doctor: DoctorUser, session: DbSession, settings: AppSettings) -> InviteOut:
    await identity.check_invite_creation(session, settings, doctor.id)
    invite = await service.create_invite(
        session, doctor.id, timedelta(days=settings.invite_ttl_days)
    )
    await identity.invite_created(session, settings, doctor.id)
    await session.commit()
    return invite


@router.post(
    "/register",
    status_code=201,
    response_model=UserOut,
    responses={
        **RATE_LIMITED_RESPONSE,
        409: {
            "description": "Consent document version mismatch or email already registered",
            "content": {PROBLEM_JSON: {"schema": ProblemOut.model_json_schema()}},
        },
    },
)
async def register(
    body: RegistrationRequest,
    request: Request,
    response: Response,
    session: DbSession,
    settings: AppSettings,
) -> UserOut | JSONResponse:
    host = request.client.host if request.client else None
    await identity.check_registration(session, settings, host=host)
    try:
        user = await service.register_patient(
            session,
            invite_token=body.invite_token,
            email=body.email,
            password=body.password,
            full_name=body.full_name,
            consent_version=body.consent_version,
        )
    except service.ConsentVersionMismatchError:
        # Не неудача для лимита: версию сверяют до приглашения и argon2, перебора адресов нет.
        return problem(
            409,
            "Consent document version does not match the current document",
            code="consent_version_mismatch",
        )
    except service.InviteUnavailableError:
        await _registration_failed(session, settings, host)
        raise HTTPException(status_code=410, detail="Invite is invalid, used or expired") from None
    except IntegrityError:
        # 409 оставляет приглашение годным: так перебирают адреса. Лимит считает и его.
        await _registration_failed(session, settings, host)
        raise HTTPException(status_code=409, detail="Email is already registered") from None
    ttl = timedelta(hours=settings.session_ttl_hours)
    token = await identity.create_session(session, user.id, ttl)
    await session.commit()
    set_session_cookie(response, token, ttl=ttl, secure=settings.cookie_secure)
    return user


async def _registration_failed(session: DbSession, settings: AppSettings, host: str | None) -> None:
    await session.rollback()
    await identity.registration_failed(session, settings, host=host)
    await session.commit()


@router.get("")
async def list_patients(doctor: DoctorUser, session: DbSession) -> list[PatientSummary]:
    patients = await service.list_patients(session, doctor.id)
    await identity.log_access(session, actor_id=doctor.id, action="patients.list")
    await session.commit()
    return patients


@router.get("/{patient_id}")
async def get_patient(
    patient: Annotated[PatientSummary, Depends(owned_patient("patient.view", deputy=True))],
) -> PatientSummary:
    return patient


@router.put("/{patient_id}/phone")
async def update_phone(
    body: PhoneUpdate,
    patient: Annotated[PatientSummary, Depends(owned_patient("patient.phone.update"))],
    session: DbSession,
) -> PatientSummary:
    """Телефон пациента для связи врача. Попытку доступа уже записал `owned_patient`."""
    updated = await service.set_phone(session, patient, body.phone)
    await session.commit()
    return updated


@me_router.post(
    "/me/consent/revoke",
    status_code=204,
    responses={
        401: {"description": "Authentication is required", "content": _PROBLEM_CONTENT},
        403: {
            "description": "Only a patient revokes their own consent",
            "content": _PROBLEM_CONTENT,
        },
    },
)
async def revoke_consent(patient: PatientUser, response: Response, session: DbSession) -> None:
    """Пациент отзывает согласие: вход и все его сессии закрываются, cookie стирается."""
    await service.revoke_consent(session, patient.id)
    await session.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")
