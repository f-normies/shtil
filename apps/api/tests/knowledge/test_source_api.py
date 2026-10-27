"""Ручка инспектора источника (спека #106, 3.3 и 3.11): только врач; 404 и 422; поля ответа."""

from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.modules.identity.schemas import Role
from clin_compass.modules.knowledge.models import Fragment
from tests.conftest import MakeUser
from tests.knowledge.helpers import Piece, add_document
from tests.knowledge.test_api import login

OHLP = "aviandr-ohlp"
URL = f"/api/v1/knowledge/sources/{OHLP}"
PIECE = Piece(
    "Противопоказания: беременность.",
    section="4.3",
    section_title="Противопоказания",
    heading_path="4. КЛИНИЧЕСКИЕ ДАННЫЕ > 4.3. Противопоказания",
)


@pytest.fixture
async def fragment(sessionmaker: async_sessionmaker[AsyncSession]) -> Fragment:
    async with sessionmaker() as session:
        _, [piece] = await add_document(
            session, OHLP, [PIECE], kind="drug_label", title="ОХЛП", version="0010"
        )
        await session.commit()
    return piece


async def test_doctor_reads_section_by_locator(
    client: httpx.AsyncClient, make_user: MakeUser, fragment: Fragment
) -> None:
    await login(client, make_user)

    response = await client.get(URL, params={"locator": "4.3: СКФ < 60 мл/мин"})

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"source", "registry", "locator", "found", "sections"}
    assert set(body["source"]) == {
        "id",
        "title",
        "kind",
        "publisher",
        "version",
        "date",
        "url",
        "supplementary",
    }
    assert (body["source"]["id"], body["source"]["kind"]) == (OHLP, "drug_label")
    assert set(body["registry"]) == {"reg_number", "status", "snapshot_date"}
    assert body["registry"]["reg_number"] == "ЛП-№(002445)-(РГ-RU)"
    assert (body["locator"], body["found"]) == ("4.3: СКФ < 60 мл/мин", True)
    [section] = body["sections"]
    assert (section["number"], section["title"]) == ("4.3", "Противопоказания")
    [text] = section["fragments"]
    assert (text["id"], text["text"]) == (str(fragment.id), PIECE.text)
    assert text["document"]["status"] == "active"


async def test_doctor_reads_fragment_by_id(
    client: httpx.AsyncClient, make_user: MakeUser, fragment: Fragment
) -> None:
    await login(client, make_user)

    response = await client.get(URL, params={"fragment_id": str(fragment.id)})

    assert response.status_code == 200
    body = response.json()
    assert (body["locator"], body["found"]) == (None, True)
    [section] = body["sections"]
    assert [f["id"] for f in section["fragments"]] == [str(fragment.id)]


async def test_section_missing_in_index_gives_found_false(
    client: httpx.AsyncClient, make_user: MakeUser
) -> None:
    await login(client, make_user)

    response = await client.get(
        "/api/v1/knowledge/sources/kr-gad-2024",
        params={"locator": "прил. Г1, ключ: HARS 18–24 — средняя выраженность"},
    )

    assert response.status_code == 200
    body = response.json()
    assert (body["found"], body["sections"], body["registry"]) == (False, [], None)
    assert body["source"]["title"] == "Генерализованное тревожное расстройство"


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"locator": "4.3", "fragment_id": str(uuid4())},
        {"locator": ""},
    ],
)
async def test_exactly_one_parameter_is_required(
    client: httpx.AsyncClient, make_user: MakeUser, params: dict[str, str]
) -> None:
    await login(client, make_user)

    response = await client.get(URL, params=params)

    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["errors"]


async def test_unknown_source_gives_404(client: httpx.AsyncClient, make_user: MakeUser) -> None:
    await login(client, make_user)

    response = await client.get("/api/v1/knowledge/sources/no-such-source", params={"locator": "1"})

    assert response.status_code == 404
    assert response.json()["detail"] == "Source not found"


async def test_fragment_of_other_source_gives_404(
    client: httpx.AsyncClient, make_user: MakeUser, fragment: Fragment
) -> None:
    await login(client, make_user)

    other = await client.get(
        "/api/v1/knowledge/sources/kr-depression-2024", params={"fragment_id": str(fragment.id)}
    )
    missing = await client.get(URL, params={"fragment_id": str(uuid4())})

    assert (other.status_code, missing.status_code) == (404, 404)
    assert other.json()["detail"] == "Fragment not found"


@pytest.mark.parametrize("role", [Role.PATIENT, Role.ADMIN])
async def test_only_doctor_opens_source(
    client: httpx.AsyncClient, make_user: MakeUser, role: Role
) -> None:
    await login(client, make_user, role)

    assert (await client.get(URL, params={"locator": "4.3"})).status_code == 403


async def test_anonymous_gets_401(client: httpx.AsyncClient) -> None:
    assert (await client.get(URL, params={"locator": "4.3"})).status_code == 401


def test_openapi_lists_source_kinds_and_registry_statuses(app: FastAPI) -> None:
    # Клиент фронтенда получает перечни значений вместо строки.
    schemas = app.openapi()["components"]["schemas"]

    kinds = schemas["SourceInfo"]["properties"]["kind"]["enum"]
    statuses = schemas["RegistryInfo"]["properties"]["status"]["enum"]
    assert {"clinical_guideline", "drug_label", "scale_validation"} <= set(kinds)
    assert statuses == ["active", "unknown", "suspended", "inactive"]
