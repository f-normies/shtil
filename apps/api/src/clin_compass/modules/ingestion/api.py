"""HTTP-роутер модуля ingestion: загрузка истории болезни, список, оригинал, повтор (спека #30, 4).

Всё только для ведущего врача: пациента выдаёт `owned_patient`, он же пишет обращение
в журнал доступа. Ответы с данными идут с `Cache-Control: no-store`.
"""

from collections import Counter
from typing import Annotated, Any
from urllib.parse import quote
from uuid import UUID

import anyio
import structlog
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from starlette.datastructures import FormData, UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.formparsers import MultiPartException
from starlette.requests import Request as StarletteRequest

from clin_compass.core.deps import DbSession
from clin_compass.core.errors import PROBLEM_JSON, ProblemOut, problem
from clin_compass.core.storage import S3Storage, StorageError, StorageObjectMissingError
from clin_compass.modules.identity.deps import DoctorUser
from clin_compass.modules.ingestion import files, service
from clin_compass.modules.ingestion.schemas import DocumentOut
from clin_compass.modules.patients.deps import owned_patient
from clin_compass.modules.patients.schemas import PatientSummary

router = APIRouter(tags=["ingestion"])
log = structlog.get_logger()

# Тело multipart больше файла на служебные части; как у голосовых — запас 1 МиБ.
MAX_MULTIPART_BYTES = service.MAX_DOCUMENT_BYTES + 1024 * 1024
_NO_STORE = {"Cache-Control": "no-store"}
_TOO_LARGE = "Document exceeds 10 MB"
_PROBLEM = {PROBLEM_JSON: {"schema": ProblemOut.model_json_schema()}}
_UPLOAD_SCHEMA: dict[str, Any] = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": ["file"],
                    "properties": {"file": {"type": "string", "format": "binary"}},
                    "additionalProperties": False,
                }
            }
        },
    }
}
_FILE_CONTENT = {
    t: {"schema": {"type": "string", "format": "binary"}}
    for t in (files.PDF, files.JPEG, files.PNG)
}


def _error(status: int, detail: str, code: str) -> JSONResponse:
    response = problem(status, detail, code=code)
    response.headers.update(_NO_STORE)
    return response


@router.post(
    "/patients/{patient_id}/documents",
    response_model=DocumentOut,
    status_code=201,
    responses={
        200: {"model": DocumentOut, "description": "The same file was uploaded before"},
        413: {"description": "document_too_large", "content": _PROBLEM},
        415: {"description": "unsupported_document_type", "content": _PROBLEM},
        422: {
            "description": "too_many_pages, unreadable_document, invalid_request",
            "content": _PROBLEM,
        },
        503: {"description": "storage_unavailable", "content": _PROBLEM},
    },
    openapi_extra=_UPLOAD_SCHEMA,
)
async def upload_document(
    request: Request,
    response: Response,
    patient: Annotated[PatientSummary, Depends(owned_patient("patient.documents.upload"))],
    doctor: DoctorUser,
    session: DbSession,
) -> DocumentOut | JSONResponse:
    """История болезни: PDF, JPEG или PNG до 10 МБ и до 6 страниц, одно поле `file`."""
    try:
        body = await _read_capped_body(request)
    except ValueError:
        return _error(413, _TOO_LARGE, "document_too_large")
    form: FormData | None = None
    try:
        form = await _replay(request, body).form(max_files=2, max_fields=2, max_part_size=64 * 1024)
        del body
        upload = _single_file(form)
        filename = upload.filename or ""
        data = await upload.read(service.MAX_DOCUMENT_BYTES + 1)
    except (MultiPartException, StarletteHTTPException, ValueError):
        return _error(422, "Invalid multipart request", "invalid_request")
    finally:
        if form is not None:
            await form.close()
    if len(data) > service.MAX_DOCUMENT_BYTES:
        return _error(413, _TOO_LARGE, "document_too_large")
    storage: S3Storage | None = getattr(request.app.state, "storage", None)
    try:
        if storage is None:
            # Сначала проверка файла: ошибка в нём важнее недоступного хранилища.
            await anyio.to_thread.run_sync(files.inspect, data)
            return _error(503, "Document storage is unavailable", "storage_unavailable")
        document, created = await service.accept_document(
            session,
            request.app.state.sessionmaker,
            storage,
            patient_id=patient.id,
            doctor_id=doctor.id,
            filename=filename,
            data=data,
        )
    except files.UnsupportedDocumentError:
        return _error(415, "Only PDF, JPEG and PNG are accepted", "unsupported_document_type")
    except files.DocumentFileError as error:
        return _error(422, "Document cannot be accepted", error.code)
    except (StorageError, service.DocumentCommitUnknownError) as error:
        log.warning("ingestion.upload_unavailable", error_type=type(error).__name__)
        return _error(503, "Document storage is unavailable", "storage_unavailable")
    response.status_code = 201 if created else 200
    response.headers.update(_NO_STORE)
    return document


@router.get("/patients/{patient_id}/documents")
async def list_documents(
    response: Response,
    patient: Annotated[PatientSummary, Depends(owned_patient("patient.documents.list"))],
    session: DbSession,
) -> list[DocumentOut]:
    """Документы пациента, новые первыми."""
    documents = await service.list_documents(session, patient.id)
    await session.commit()
    response.headers.update(_NO_STORE)
    return documents


@router.get(
    "/patients/{patient_id}/documents/{document_id}/file",
    response_class=Response,
    responses={
        200: {"description": "Original document", "content": _FILE_CONTENT},
        404: {"description": "Document not found", "content": _PROBLEM},
        503: {"description": "storage_unavailable", "content": _PROBLEM},
    },
)
async def document_file(
    document_id: UUID,
    request: Request,
    patient: Annotated[PatientSummary, Depends(owned_patient("patient.documents.file"))],
    session: DbSession,
) -> Response:
    """Оригинал документа с исходным типом."""
    try:
        stored = await service.stored_file(session, patient.id, document_id)
    except service.DocumentNotFoundError:
        return _error(404, "Document not found", "document_not_found")
    finally:
        await session.commit()
    storage: S3Storage | None = getattr(request.app.state, "storage", None)
    if storage is None:
        return _error(503, "Document storage is unavailable", "storage_unavailable")
    try:
        data = await anyio.to_thread.run_sync(_read_object, storage, stored.object_key)
    except StorageObjectMissingError:
        log.error("ingestion.original_missing", document_id=str(document_id))
        return _error(404, "Document not found", "document_not_found")
    except (StorageError, OSError) as error:
        log.warning("ingestion.file_unavailable", error_type=type(error).__name__)
        return _error(503, "Document storage is unavailable", "storage_unavailable")
    disposition = f"inline; filename*=UTF-8''{quote(stored.filename)}"
    return Response(
        data,
        media_type=stored.content_type,
        headers={**_NO_STORE, "Content-Disposition": disposition},
    )


@router.post(
    "/patients/{patient_id}/documents/{document_id}/retry",
    response_model=DocumentOut,
    responses={
        404: {"description": "Document not found", "content": _PROBLEM},
        409: {"description": "document_not_failed", "content": _PROBLEM},
    },
)
async def retry_document(
    document_id: UUID,
    response: Response,
    patient: Annotated[PatientSummary, Depends(owned_patient("patient.documents.retry"))],
    session: DbSession,
) -> DocumentOut | JSONResponse:
    """Повторить этап документа `failed`: распознавание или извлечение."""
    try:
        document = await service.retry(session, patient.id, document_id)
    except service.DocumentNotFoundError:
        await session.rollback()
        return _error(404, "Document not found", "document_not_found")
    except service.DocumentNotFailedError:
        await session.rollback()
        return _error(409, "Only a failed document can be retried", "document_not_failed")
    await session.commit()
    response.headers.update(_NO_STORE)
    return document


async def _read_capped_body(request: Request) -> bytes:
    """Тело запроса не больше `MAX_MULTIPART_BYTES`, с `Content-Length` и без него."""
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > MAX_MULTIPART_BYTES:
        raise ValueError("body too large")
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_MULTIPART_BYTES:
            raise ValueError("body too large")
        chunks.append(chunk)
    return b"".join(chunks)


def _replay(request: Request, body: bytes) -> StarletteRequest:
    """Запрос с уже прочитанным телом: разбор multipart идёт по нему."""
    delivered = False

    async def receive() -> dict[str, object]:
        nonlocal delivered
        if delivered:
            return {"type": "http.request", "body": b"", "more_body": False}
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    return StarletteRequest(request.scope, receive=receive)


def _single_file(form: FormData) -> UploadFile:
    parts = form.multi_items()
    if Counter(name for name, _ in parts) != Counter({"file": 1}):
        raise ValueError("invalid multipart fields")
    upload = parts[0][1]
    if not isinstance(upload, UploadFile):
        raise ValueError("invalid multipart file")
    return upload


def _read_object(storage: S3Storage, key: str) -> bytes:
    stored = storage.get(key)
    try:
        return stored.body.read(service.MAX_DOCUMENT_BYTES + 1)
    finally:
        stored.body.close()
