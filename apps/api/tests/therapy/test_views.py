"""Ответ подбора для экрана «Терапия» без БД (спека #28, раздел 8.2; кадр «Врач v4 · 10»).

Препарат — выдуманный `test-drug` из `tests/therapy/knowledge`: форма 10 мг, показание
`course` с кодом F43.2.
"""

from pathlib import Path

from clin_compass.modules.therapy import views
from clin_compass.modules.therapy.checks import Catalog

CATALOG = Catalog.load(Path(__file__).parent / "knowledge")
DRUG = CATALOG.drugs["test-drug"]
COURSE = next(i for i in DRUG.indications if i.id == "course")


def test_option_gives_form_strength_for_the_tablet_count() -> None:
    # Число таблеток в строке схемы считается из дозы и крепости формы.
    option = views.option(CATALOG, DRUG, COURSE, [], None)

    assert option.regimen.strength_mg == 10


def test_option_gives_indication_codes_from_the_card() -> None:
    # «Показание по инструкции: {показание}, {коды}» — коды из карточки препарата.
    option = views.option(CATALOG, DRUG, COURSE, [], None)

    assert option.indication_icd10 == ["F43.2"]


def test_proposal_payload_keeps_strength_and_codes() -> None:
    # Открытое предложение экран показывает из `payload`, без повторного подбора.
    payload = views.payload(CATALOG, DRUG, COURSE, [])

    assert payload["regimen"]["strength_mg"] == 10
    assert payload["indication_icd10"] == ["F43.2"]


def test_source_link_gives_document_version_and_date() -> None:
    # Спека #28, 8.7: у источника схемы — версия и дата документа из манифеста.
    option = views.option(CATALOG, DRUG, COURSE, [], None)

    assert option.regimen.source.version == "1"
    assert option.regimen.source.date.isoformat() == "2025-01-01"


def test_option_gives_registration_number_status_and_snapshot_date() -> None:
    # Спека #28, 8.7: номер РУ из карточки, статус и дата снимка ГРЛС.
    option = views.option(CATALOG, DRUG, COURSE, [], None)

    assert option.registration is not None
    assert option.registration.reg_number == "ЛП-000100"
    assert option.registration.status == "active"
    assert option.registration.snapshot_date.isoformat() == "2025-01-01"


def test_proposal_payload_keeps_registration_and_source_versions() -> None:
    # Снимок предложения становится снимком назначения: версия документа и РУ в нём.
    payload = views.payload(CATALOG, DRUG, COURSE, [])

    assert payload["registration"]["reg_number"] == "ЛП-000100"
    assert payload["regimen"]["source"]["version"] == "1"
