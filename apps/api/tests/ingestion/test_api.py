"""HTTP: загрузка истории болезни, список, оригинал и повтор (спека #30, разделы 3.2 и 4)."""

import hashlib
from collections.abc import AsyncIterator

import httpx
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.modules.ingestion.models import Document
from tests.freetext.conftest import Care, make_care
from tests.ingestion.helpers import (
    BrokenStorage,
    MemoryStorage,
    document,
    jobs,
    login,
    upload,
)
from tests.ingestion.samples import image, pdf
from tests.patients.test_access import access_log

MAX = 10 * 1024 * 1024


def documents_url(care: Care) -> str:
    return f"/api/v1/patients/{care.patient.id}/documents"


async def count_documents(maker: async_sessionmaker[AsyncSession]) -> int:
    async with maker() as session:
        return int(await session.scalar(select(func.count()).select_from(Document)) or 0)


async def test_lead_doctor_uploads_pdf(
    app: FastAPI,
    client: httpx.AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    care: Care,
) -> None:
    store = MemoryStorage()
    app.state.storage = store
    await login(client, care.doctor.email)
    data = pdf(2)

    response = await upload(client, care.patient.id, data, filename="../Выписка.pdf")

    assert response.status_code == 201, response.text
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert (body["status"], body["pages"], body["content_type"]) == (
        "parsing",
        2,
        "application/pdf",
    )
    assert (body["filename"], body["size"], body["sha256"]) == (
        "Выписка.pdf",
        len(data),
        hashlib.sha256(data).hexdigest(),
    )
    assert "text" not in body and "object_key" not in body
    saved = await document(sessionmaker, body["id"])
    assert saved.object_key.startswith("documents/")
    assert store.objects[saved.object_key] == (data, "application/pdf")
    assert await jobs(sessionmaker) == [("ingestion.parse", body["id"].encode())]


async def test_same_file_returns_the_same_document(
    app: FastAPI,
    client: httpx.AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    care: Care,
) -> None:
    store = MemoryStorage()
    app.state.storage = store
    await login(client, care.doctor.email)
    data = pdf()
    first = await upload(client, care.patient.id, data)

    again = await upload(client, care.patient.id, data, filename="копия.pdf")

    assert (first.status_code, again.status_code) == (201, 200)
    assert again.json()["id"] == first.json()["id"]
    assert len(store.objects) == 1
    assert len(await jobs(sessionmaker)) == 1


async def test_type_comes_from_signature_not_header(
    app: FastAPI, client: httpx.AsyncClient, care: Care
) -> None:
    app.state.storage = MemoryStorage()
    await login(client, care.doctor.email)

    response = await upload(
        client, care.patient.id, image("PNG"), filename="scan.pdf", content_type="application/pdf"
    )

    assert response.status_code == 201
    assert (response.json()["content_type"], response.json()["pages"]) == ("image/png", 1)


async def test_file_over_10_mb_is_rejected(
    app: FastAPI, client: httpx.AsyncClient, care: Care
) -> None:
    app.state.storage = MemoryStorage()
    await login(client, care.doctor.email)

    response = await upload(client, care.patient.id, pdf() + b"\0" * (MAX + 1 - len(pdf())))

    assert response.status_code == 413
    assert response.json()["code"] == "document_too_large"


async def test_file_over_10_mb_without_content_length_is_rejected(
    app: FastAPI, client: httpx.AsyncClient, care: Care
) -> None:
    app.state.storage = MemoryStorage()
    await login(client, care.doctor.email)
    boundary = "document-boundary"
    head = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="big.pdf"\r\n'
        "Content-Type: application/pdf\r\n\r\n"
    ).encode()

    async def body() -> AsyncIterator[bytes]:
        yield head + b"%PDF-"
        for _ in range(11):
            yield b"\0" * (1024 * 1024)
        yield f"\r\n--{boundary}--\r\n".encode()

    response = await client.post(
        documents_url(care),
        content=body(),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )

    assert response.status_code == 413
    assert response.json()["code"] == "document_too_large"


async def test_exactly_10_mb_part_over_limit_by_one_byte(
    app: FastAPI, client: httpx.AsyncClient, care: Care
) -> None:
    # Тело с файлом в 10 МБ + 1 байт укладывается в предел multipart, но не в предел файла.
    app.state.storage = MemoryStorage()
    await login(client, care.doctor.email)
    head = b"%PDF-1.7\n"

    response = await upload(client, care.patient.id, head + b"\0" * (MAX + 1 - len(head)))

    assert response.status_code == 413
    assert response.json()["code"] == "document_too_large"


async def test_unreadable_files_are_rejected(
    app: FastAPI,
    client: httpx.AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    care: Care,
) -> None:
    app.state.storage = MemoryStorage()
    await login(client, care.doctor.email)

    seven = await upload(client, care.patient.id, pdf(7))
    broken = await upload(client, care.patient.id, b"%PDF-1.7\nbroken")
    gif = await upload(client, care.patient.id, b"GIF89a....", content_type="image/png")

    assert (seven.status_code, seven.json()["code"]) == (422, "too_many_pages")
    assert (broken.status_code, broken.json()["code"]) == (422, "unreadable_document")
    assert (gif.status_code, gif.json()["code"]) == (415, "unsupported_document_type")
    assert await count_documents(sessionmaker) == 0


async def test_multipart_needs_exactly_one_file_field(
    app: FastAPI, client: httpx.AsyncClient, care: Care
) -> None:
    app.state.storage = MemoryStorage()
    await login(client, care.doctor.email)

    response = await client.post(
        documents_url(care),
        data={"note": "x"},
        files={"file": ("a.pdf", pdf(), "application/pdf")},
    )

    assert response.status_code == 422


async def test_other_doctor_gets_404_and_denial_is_logged(
    app: FastAPI,
    client: httpx.AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    care: Care,
) -> None:
    app.state.storage = MemoryStorage()
    other = await make_care(sessionmaker, "other@example.com", "other-patient@example.com")
    await login(client, other.doctor.email)

    response = await upload(client, care.patient.id, pdf())

    assert response.status_code == 404
    assert (
        other.doctor.id,
        "patient.documents.upload.denied",
        care.patient.id,
    ) in await access_log(sessionmaker)
    assert await count_documents(sessionmaker) == 0


async def test_patient_cannot_upload(app: FastAPI, client: httpx.AsyncClient, care: Care) -> None:
    app.state.storage = MemoryStorage()
    await login(client, care.patient.email)

    response = await upload(client, care.patient.id, pdf())

    assert response.status_code == 403


async def test_storage_failure_leaves_no_document_and_no_job(
    app: FastAPI,
    client: httpx.AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    care: Care,
) -> None:
    app.state.storage = BrokenStorage()
    await login(client, care.doctor.email)

    response = await upload(client, care.patient.id, pdf())

    assert response.status_code == 503
    assert response.json()["code"] == "storage_unavailable"
    assert await count_documents(sessionmaker) == 0
    assert await jobs(sessionmaker) == []


async def test_storage_not_configured_is_503(client: httpx.AsyncClient, care: Care) -> None:
    await login(client, care.doctor.email)

    response = await upload(client, care.patient.id, pdf())

    assert (response.status_code, response.json()["code"]) == (503, "storage_unavailable")


async def test_documents_are_listed_newest_first(
    app: FastAPI, client: httpx.AsyncClient, care: Care
) -> None:
    app.state.storage = MemoryStorage()
    await login(client, care.doctor.email)
    first = (await upload(client, care.patient.id, pdf(1))).json()
    second = (await upload(client, care.patient.id, pdf(2))).json()

    response = await client.get(documents_url(care))

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert [d["id"] for d in response.json()] == [second["id"], first["id"]]
    assert all("text" not in d for d in response.json())


async def test_original_is_given_only_to_lead_doctor(
    app: FastAPI,
    client: httpx.AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    care: Care,
) -> None:
    app.state.storage = MemoryStorage()
    await login(client, care.doctor.email)
    data = image("JPEG")
    created = (await upload(client, care.patient.id, data, filename="фото.jpg")).json()
    url = f"{documents_url(care)}/{created['id']}/file"

    own = await client.get(url)
    other = await make_care(sessionmaker, "other@example.com", "other-patient@example.com")
    await login(client, other.doctor.email)
    foreign = await client.get(url)

    assert own.status_code == 200
    assert own.content == data
    assert own.headers["content-type"] == "image/jpeg"
    assert own.headers["cache-control"] == "no-store"
    assert foreign.status_code == 404


async def test_document_of_another_patient_is_not_found(
    app: FastAPI,
    client: httpx.AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    care: Care,
) -> None:
    app.state.storage = MemoryStorage()
    await login(client, care.doctor.email)
    created = (await upload(client, care.patient.id, pdf())).json()
    second = await make_care(sessionmaker, "second@example.com", "second-patient@example.com")
    await login(client, second.doctor.email)
    own = (await upload(client, second.patient.id, pdf(2))).json()

    # Свой пациент в пути, чужой документ: ответ тот же, что и для несуществующего.
    response = await client.get(f"{documents_url(second)}/{created['id']}/file")

    assert response.status_code == 404
    assert own["patient_id"] == str(second.patient.id)
