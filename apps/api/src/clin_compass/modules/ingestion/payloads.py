"""`payload` предложений и факты, которые уже есть в карточке (спека #30, 3.4 и 4).

Диагноз с кодом действующего диагноза пациента и текущий препарат с тем же нормализованным
названием предложениями не приходят: подтверждение упало бы с «уже есть». Они пишутся
в `known` документа, и экран показывает их в группе «Уже в карточке».
"""

from collections.abc import Sequence
from datetime import date
from typing import Any

from clin_compass_knowledge.grls import DrugCheck
from sqlalchemy.ext.asyncio import AsyncSession

from clin_compass.modules.ingestion.analysis import Extraction, FoundMedication, name_key
from clin_compass.modules.ingestion.document import PageSpan, quote_page
from clin_compass.modules.ingestion.models import Document
from clin_compass.modules.ingestion.schemas import (
    DIAGNOSIS_KIND,
    FACT_KIND,
    MEDICATION_KIND,
    DiagnosisPayload,
    DrugCheckOut,
    DrugMatchOut,
    FactPayload,
    KnownFact,
    MedicationPayload,
)
from clin_compass.modules.patients import service as patients
from clin_compass.modules.therapy import service as therapy

Offer = tuple[str, dict[str, Any]]


def is_current(stopped_on: date | None, today: date) -> bool:
    """Препарат не выбыл: даты отмены нет или она ещё не наступила."""
    return stopped_on is None or stopped_on > today


async def card_split(
    session: AsyncSession,
    document: Document,
    result: Extraction,
    text: str,
    spans: Sequence[PageSpan],
) -> tuple[list[Offer], list[dict[str, Any]]]:
    """Предложения (вид и `payload`) и факты, которые уже есть в карточке пациента."""
    diagnoses = await patients.list_diagnoses(session, document.patient_id)
    active_codes = {d.icd10 for d in diagnoses if d.resolved_at is None}
    today = therapy.moscow_today()
    medications = await therapy.list_medications(session, document.patient_id)
    current = {name_key(m.name) for m in medications if is_current(m.stopped_on, today)}

    def common(quote: str) -> dict[str, Any]:
        return {
            "document_id": document.id,
            "quote": quote,
            "page": quote_page(quote, text, spans),
            "ocr": bool(document.ocr),
        }

    offers: list[Offer] = []
    known: list[dict[str, Any]] = []
    for found in result.diagnoses:
        diagnosis = DiagnosisPayload(
            **common(found.quote),
            icd10=found.icd10,
            text=found.text,
            code_in_text=found.code_in_text,
        )
        if found.icd10 in active_codes:
            known.append(_known(DIAGNOSIS_KIND, diagnosis))
        else:
            offers.append((DIAGNOSIS_KIND, diagnosis.model_dump(mode="json")))
    for drug in result.medications:
        medication = _medication(common(drug.quote), drug, result.document_date)
        if drug.status == "current" and name_key(drug.name) in current:
            known.append(_known(MEDICATION_KIND, medication))
        else:
            offers.append((MEDICATION_KIND, medication.model_dump(mode="json")))
    for fact in result.facts:
        payload = FactPayload(**common(fact.quote), field=fact.field, fact=fact.fact)
        offers.append((FACT_KIND, payload.model_dump(mode="json")))
    return offers, known


def _medication(
    common: dict[str, Any], drug: FoundMedication, document_date: date | None
) -> MedicationPayload:
    return MedicationPayload(
        **common,
        name=drug.name,
        name_in_text=drug.name_in_text,
        renamed=drug.renamed,
        dose=drug.dose,
        regimen=drug.regimen,
        status=drug.status,
        stopped_on=drug.stopped_on,
        stopped_on_source=drug.stopped_on_source,
        document_date=document_date,
        drug=_check(drug.drug),
    )


def _check(check: DrugCheck) -> DrugCheckOut:
    return DrugCheckOut(
        name=check.name,
        check=check.check,
        matches=[
            DrugMatchOut(trade_name=m.trade_name, inn=m.inn, reg_number=m.reg_number)
            for m in check.matches
        ],
    )


def _known(kind: str, payload: DiagnosisPayload | MedicationPayload) -> dict[str, Any]:
    return KnownFact.model_validate({"kind": kind, "payload": payload}).model_dump(mode="json")
