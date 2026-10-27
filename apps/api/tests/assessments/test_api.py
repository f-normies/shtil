"""HTTP-контракт каталога шкал и описание ответов в OpenAPI."""

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from tests.assessments.conftest import BASE, AssessmentCare, login, submission
from tests.conftest import MakeUser

from clin_compass.modules.assessments.service import ScaleCatalog
from clin_compass.modules.identity.schemas import Role, UserOut


async def test_patient_catalog_lists_only_eligible_scales_with_source_metadata(
    assessment_client: AsyncClient, make_user: MakeUser
) -> None:
    patient: UserOut = await make_user(email="catalog-patient@example.com", role=Role.PATIENT)
    await login(assessment_client, patient.email)

    listed = await assessment_client.get("/api/v1/assessments/scales")

    assert listed.status_code == 200
    body = listed.json()
    assert len(body["items"]) == 2
    item = body["items"][0]
    assert item["scale"]["id"] == "test-eligible"
    assert item["scale"]["scores"][0]["id"] == "total"
    assert [(source["id"], source["supplementary"]) for source in item["sources"]] == [
        ("test-scale-validation", False),
        ("test-validation", True),
    ]

    detail = await assessment_client.get("/api/v1/assessments/scales/test-eligible")
    unavailable = await assessment_client.get("/api/v1/assessments/scales/test-draft")

    assert detail.status_code == 200
    assert detail.json() == item
    assert unavailable.status_code == 404
    assert unavailable.json()["code"] == "scale_unavailable"


async def test_scale_catalog_requires_patient_authentication(
    assessment_client: AsyncClient, make_user: MakeUser
) -> None:
    anonymous = await assessment_client.get("/api/v1/assessments/scales")
    doctor: UserOut = await make_user(email="catalog-doctor@example.com")
    await login(assessment_client, doctor.email)
    wrong_role = await assessment_client.get("/api/v1/assessments/scales")

    assert anonymous.status_code == 401
    assert wrong_role.status_code == 403


async def test_doctor_reads_scale_definition_for_patient_history(
    assessment_client: AsyncClient, make_user: MakeUser
) -> None:
    # Карточка пациента (#13) показывает текст вопросов и зоны тяжести из определения.
    doctor: UserOut = await make_user(email="definition-doctor@example.com")
    await login(assessment_client, doctor.email)

    detail = await assessment_client.get("/api/v1/assessments/scales/test-eligible")
    unavailable = await assessment_client.get("/api/v1/assessments/scales/test-draft")

    assert detail.status_code == 200
    assert detail.json()["scale"]["id"] == "test-eligible"
    assert unavailable.status_code == 404
    assert unavailable.json()["code"] == "scale_unavailable"


async def test_scale_definition_requires_authentication(assessment_client: AsyncClient) -> None:
    response = await assessment_client.get("/api/v1/assessments/scales/test-eligible")

    assert response.status_code == 401


async def test_empty_scale_catalog_returns_an_empty_items_list(
    empty_assessment_client: AsyncClient, make_user: MakeUser
) -> None:
    patient: UserOut = await make_user(email="empty-catalog@example.com", role=Role.PATIENT)
    await login(empty_assessment_client, patient.email)

    response = await empty_assessment_client.get("/api/v1/assessments/scales")

    assert response.status_code == 200
    assert response.json() == {"items": []}


async def test_openapi_documents_assessment_replay_and_problem_responses(
    assessment_app: FastAPI,
) -> None:
    document = assessment_app.openapi()
    post = document["paths"]["/api/v1/assessments"]["post"]
    responses = post["responses"]

    assert {"200", "201", "401", "403", "404", "409", "422", "503"} <= set(responses)
    for status in ("401", "403", "404", "409", "503"):
        content = responses[status]["content"]
        assert set(content) == {"application/problem+json"}
        schema = content["application/problem+json"]["schema"]
        assert "code" in schema["properties"]
    assert (
        "current_version"
        in (responses["409"]["content"]["application/problem+json"]["schema"]["properties"])
    )
    validation_union = responses["422"]["content"]["application/problem+json"]["schema"]
    variants = validation_union["anyOf"]
    assert len(variants) == 2
    assert any("code" in variant["properties"] for variant in variants)
    assert any("errors" in variant["required"] for variant in variants)

    for status in ("200", "201"):
        assert "application/json" in responses[status]["content"]
    unavailable = document["paths"]["/api/v1/assessments/scales/{scale_id}"]["get"]["responses"][
        "404"
    ]["content"]
    assert set(unavailable) == {"application/problem+json"}

    for path in (
        "/api/v1/assessments/scales",
        "/api/v1/assessments/scales/{scale_id}",
    ):
        problem_content = document["paths"][path]["get"]["responses"]["503"]["content"]
        assert set(problem_content) == {"application/problem+json"}
        assert "code" in problem_content["application/problem+json"]["schema"]["properties"]

    history_codes = {
        "/api/v1/assessments": {"401", "403"},
        "/api/v1/patients/{patient_id}/assessments": {"401", "403", "404"},
    }
    for path, codes in history_codes.items():
        history = document["paths"][path]["get"]["responses"]
        assert codes <= set(history)
        for status in codes:
            assert set(history[status]["content"]) == {"application/problem+json"}


@pytest.mark.parametrize(
    "path",
    ["/api/v1/assessments/scales", "/api/v1/assessments/scales/test-eligible"],
)
async def test_catalog_reads_refuse_a_known_definition_changed_without_version_bump(
    assessment_client: AsyncClient,
    assessment_app: FastAPI,
    assessment_care: AssessmentCare,
    path: str,
) -> None:
    await login(assessment_client, assessment_care.patient.email)
    created = await assessment_client.post(BASE, json=submission(str(assessment_care.patient.id)))
    assert created.status_code == 201

    catalog = assessment_app.state.assessment_catalog
    original_scale = catalog.scales["test-eligible"]
    changed_item = original_scale.items[0].model_copy(update={"text": "Изменённый смысл вопроса"})
    changed_scale = original_scale.model_copy(
        update={"items": (changed_item, *original_scale.items[1:])}
    )
    assessment_app.state.assessment_catalog = ScaleCatalog(
        scales={**catalog.scales, "test-eligible": changed_scale}, sources=catalog.sources
    )

    response = await assessment_client.get(path)

    assert response.status_code == 503
    assert response.json()["code"] == "scale_definition_conflict"


async def test_catalog_check_compares_every_accepted_pair_in_one_listing(
    assessment_client: AsyncClient,
    assessment_app: FastAPI,
    assessment_care: AssessmentCare,
) -> None:
    await login(assessment_client, assessment_care.patient.email)
    for body in (
        submission(str(assessment_care.patient.id)),
        submission(
            str(assessment_care.patient.id), scale_id="test-eligible-second", answers={"q3": 0}
        ),
    ):
        created = await assessment_client.post(BASE, json=body)
        assert created.status_code == 201

    catalog = assessment_app.state.assessment_catalog
    second = catalog.scales["test-eligible-second"]
    changed = second.model_copy(update={"instruction": "Изменённая инструкция"})
    assessment_app.state.assessment_catalog = ScaleCatalog(
        scales={**catalog.scales, "test-eligible-second": changed}, sources=catalog.sources
    )

    listing = await assessment_client.get(f"{BASE}/scales")
    unchanged = await assessment_client.get(f"{BASE}/scales/test-eligible")
    conflicting = await assessment_client.get(f"{BASE}/scales/test-eligible-second")

    assert listing.status_code == 503
    assert listing.json()["code"] == "scale_definition_conflict"
    assert unchanged.status_code == 200
    assert conflicting.status_code == 503
