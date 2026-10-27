"""HTTP endpoints дневника; ownership и журналирование остаются в service."""

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, FastAPI, Query, Request, Response
from fastapi.responses import JSONResponse

from clin_compass.core.deps import AppSettings, DbSession
from clin_compass.core.errors import problem
from clin_compass.modules.diary import audio_api, service, voice_api
from clin_compass.modules.diary.errors import DiaryError
from clin_compass.modules.diary.schemas import (
    DiaryCommandReceipt,
    DiaryContextOut,
    DiaryDoctorEntryPage,
    DiaryEntryCreateIn,
    DiaryEntryOut,
    DiaryEntryPage,
    DiaryEntryPatchIn,
    DiaryFilter,
    DiaryHideIn,
    DiaryMoodPointsOut,
    DiaryVoiceUploadMetadataIn,
    IntakeMarkCreateIn,
    IntakeMarkOut,
    IntakeMarksOut,
)
from clin_compass.modules.identity.deps import CurrentUser

router = APIRouter(tags=["diary"])


def register_voice_upload_openapi_schema(app: FastAPI) -> None:
    """Register the parser-only multipart metadata model in OpenAPI components."""
    document = app.openapi()
    metadata_schema = DiaryVoiceUploadMetadataIn.model_json_schema(
        ref_template="#/components/schemas/{model}"
    )
    definitions = metadata_schema.pop("$defs", {})
    schemas = document.setdefault("components", {}).setdefault("schemas", {})
    schemas.update(definitions)
    schemas["DiaryVoiceUploadMetadataIn"] = metadata_schema


async def handle_error(_request: Request, error: Exception) -> JSONResponse:
    """Вернуть стабильный problem+json ответ дневника."""
    assert isinstance(error, DiaryError)
    return problem(error.status, error.detail, code=error.code, **error.fields)


@router.get("/diary/context", response_model=DiaryContextOut)
async def get_context(
    response: Response, actor: CurrentUser, session: DbSession, settings: AppSettings
) -> DiaryContextOut:
    result = await service.context(session, actor, timezone=settings.timezone)
    await session.commit()
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get("/diary/entries", response_model=DiaryEntryPage)
async def list_entries(
    response: Response,
    actor: CurrentUser,
    session: DbSession,
    settings: AppSettings,
    filter_: Annotated[DiaryFilter, Query(alias="filter")] = "all",
    start: date | None = None,
    end: date | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> DiaryEntryPage:
    result = await service.list_patient_entries(
        session,
        actor,
        timezone=settings.timezone,
        filter_=filter_,
        start=start,
        end=end,
        cursor=cursor,
        limit=limit,
    )
    await session.commit()
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post(
    "/diary/entries",
    response_model=DiaryCommandReceipt,
    status_code=201,
    responses={200: {"model": DiaryCommandReceipt, "description": "Idempotent replay"}},
)
async def create_entry(
    body: DiaryEntryCreateIn,
    response: Response,
    actor: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> DiaryCommandReceipt:
    result, created = await service.create_entry(session, actor, body, timezone=settings.timezone)
    await session.commit()
    response.status_code = 201 if created else 200
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get("/diary/entries/{entry_id}", response_model=DiaryEntryOut)
async def get_entry(
    entry_id: UUID,
    response: Response,
    actor: CurrentUser,
    session: DbSession,
) -> DiaryEntryOut:
    result = await service.get_patient_entry(session, actor, entry_id)
    await session.commit()
    response.headers["Cache-Control"] = "no-store"
    return result


@router.patch(
    "/diary/entries/{entry_id}",
    response_model=DiaryCommandReceipt,
    responses={200: {"model": DiaryCommandReceipt}},
)
async def patch_entry(
    entry_id: UUID,
    body: DiaryEntryPatchIn,
    response: Response,
    actor: CurrentUser,
    session: DbSession,
) -> DiaryCommandReceipt:
    result, _created = await service.patch_entry(session, actor, entry_id, body)
    await session.commit()
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post(
    "/diary/entries/{entry_id}/hide",
    response_model=DiaryCommandReceipt,
    responses={200: {"model": DiaryCommandReceipt}},
)
async def hide_entry(
    entry_id: UUID,
    body: DiaryHideIn,
    response: Response,
    actor: CurrentUser,
    session: DbSession,
) -> DiaryCommandReceipt:
    result, _created = await service.hide_entry(session, actor, entry_id, body)
    await session.commit()
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get("/diary/intake", response_model=IntakeMarksOut)
async def list_own_intake(
    response: Response,
    actor: CurrentUser,
    session: DbSession,
    settings: AppSettings,
    scheduled_on: date | None = None,
) -> IntakeMarksOut:
    if scheduled_on is None:
        from datetime import UTC, datetime

        scheduled_on = datetime.now(UTC).astimezone(settings.timezone).date()
    result = await service.list_patient_intake_marks(session, actor, scheduled_on=scheduled_on)
    await session.commit()
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post(
    "/diary/intake",
    response_model=IntakeMarkOut,
    status_code=201,
    responses={200: {"model": IntakeMarkOut, "description": "Idempotent replay"}},
)
async def create_intake(
    body: IntakeMarkCreateIn,
    response: Response,
    actor: CurrentUser,
    session: DbSession,
) -> IntakeMarkOut:
    result, created = await service.create_intake_mark(session, actor, body)
    await session.commit()
    response.status_code = 201 if created else 200
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get("/patients/{patient_id}/diary", response_model=DiaryDoctorEntryPage)
async def list_patient_diary(
    patient_id: UUID,
    response: Response,
    actor: CurrentUser,
    session: DbSession,
    settings: AppSettings,
    start: date,
    end: date,
    filter_: Annotated[DiaryFilter, Query(alias="filter")] = "all",
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> DiaryDoctorEntryPage:
    result = await service.list_doctor_entries(
        session,
        doctor_id=actor.id,
        patient_id=patient_id,
        start=start,
        end=end,
        timezone=settings.timezone,
        filter_=filter_,
        cursor=cursor,
        limit=limit,
    )
    await session.commit()
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get("/patients/{patient_id}/diary/moods", response_model=DiaryMoodPointsOut)
async def list_patient_moods(
    patient_id: UUID,
    response: Response,
    actor: CurrentUser,
    session: DbSession,
    settings: AppSettings,
    start: date,
    end: date,
) -> DiaryMoodPointsOut:
    result = await service.doctor_moods(
        session,
        doctor_id=actor.id,
        patient_id=patient_id,
        start=start,
        end=end,
        timezone=settings.timezone,
    )
    await session.commit()
    response.headers["Cache-Control"] = "no-store"
    return result


router.include_router(voice_api.router)
router.include_router(audio_api.router)
