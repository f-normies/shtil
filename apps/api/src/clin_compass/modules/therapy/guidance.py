"""Варианты терапии по клиническим рекомендациям: профиль, ключ, вопрос и строки справки
(спека #107, разделы 4–5).

Профиль справки — коды активных диагнозов, пол, возрастная группа и источники вопроса.
ФИО, контакты и дата рождения в профиль не входят: по профилю задача строит вопрос модели.
Ключ профиля — SHA-256 его JSON: готовый ответ с тем же ключом подходит любому пациенту
и копируется без вызова модели. Коммитит вызывающий.
"""

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, cast
from uuid import UUID

from clin_compass_knowledge.safety import icd10_matches
from clin_compass_knowledge.schemas.source import Source
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from clin_compass.core.queue import enqueue
from clin_compass.modules.knowledge.schemas import Answer
from clin_compass.modules.patients import service as patients
from clin_compass.modules.patients.schemas import DiagnosisOut, PatientSummary
from clin_compass.modules.therapy.checks import Catalog, age_on
from clin_compass.modules.therapy.models import Guidance
from clin_compass.modules.therapy.schemas import (
    AgeGroup,
    GuidanceOut,
    GuidanceProfileOut,
    GuidelineOut,
)

ENTRYPOINT = "therapy.guidance"
# После третьей неудачи задачи строки получают `failed`, врач видит «Повторить».
FAILURE_LIMIT = 3
_GUIDELINE = "clinical_guideline"
_ADULT = 18
_ELDERLY = 65
_WHOM: dict[AgeGroup | None, str] = {
    "18–64": "взрослому",
    "≥65": "пациенту 65 лет и старше",
    "<18": "пациенту младше 18 лет",
    None: "пациенту",
}
_AGE_TEXT: dict[AgeGroup | None, str] = {
    "18–64": "18–64 года",
    "≥65": "65 лет и старше",
    "<18": "младше 18 лет",
    None: "не указан",
}
_SEX_TEXT = {"female": "женский", "male": "мужской"}
_QUESTION = (
    "Какие варианты фармакотерапии и немедикаментозного лечения рекомендованы {whom} при {codes}?"
)


@dataclass(frozen=True)
class GuidanceProfile:
    """Что о пациенте нужно справке. Коды и источники отсортированы."""

    diagnoses: tuple[str, ...]
    sex: str | None
    age_group: AgeGroup | None
    source_ids: tuple[str, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "diagnoses": list(self.diagnoses),
            "sex": self.sex,
            "age_group": self.age_group,
            "source_ids": list(self.source_ids),
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "GuidanceProfile":
        return cls(
            diagnoses=tuple(data["diagnoses"]),
            sex=data["sex"],
            age_group=cast(AgeGroup | None, data["age_group"]),
            source_ids=tuple(data["source_ids"]),
        )

    @property
    def key(self) -> str:
        """SHA-256 от JSON профиля с отсортированными ключами: 64 знака."""
        raw = json.dumps(self.to_json(), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(raw.encode()).hexdigest()


def age_group(birth_date: date | None, today: date) -> AgeGroup | None:
    """Возрастная группа на дату. Без даты рождения — None."""
    if birth_date is None:
        return None
    years = age_on(birth_date, today)
    if years < _ADULT:
        return "<18"
    return "18–64" if years < _ELDERLY else "≥65"


def guideline_ids(sources: Mapping[str, Source], diagnoses: Iterable[str]) -> list[str]:
    """КР манифеста, у которых `icd10` покрывает хотя бы один диагноз. Отсортированы по id."""
    codes = list(diagnoses)
    return sorted(
        s.id
        for s in sources.values()
        if s.kind == _GUIDELINE and any(icd10_matches(c, s.icd10) for c in codes)
    )


def build_profile(
    catalog: Catalog, patient: PatientSummary, diagnoses: Sequence[DiagnosisOut], today: date
) -> GuidanceProfile | None:
    """Профиль справки по активным диагнозам. Нет активных диагнозов — None.

    Источники вопроса — КР к диагнозам и ОХЛП препаратов справочника. Справочник обновился —
    ключ меняется, и ответ считается заново.
    """
    codes = tuple(sorted({d.icd10 for d in diagnoses if d.resolved_at is None}))
    if not codes:
        return None
    labels = {drug.label for drug in catalog.drugs.values()}
    return GuidanceProfile(
        diagnoses=codes,
        sex=patient.sex,
        age_group=age_group(patient.birth_date, today),
        source_ids=tuple(sorted({*guideline_ids(catalog.sources, codes), *labels})),
    )


def question(profile: GuidanceProfile, sources: Mapping[str, Source]) -> str:
    """Вопрос модели: группа пациента, коды и названия КР к каждому коду.

    Название КР берётся из манифеста, только если КР есть в источниках профиля.
    """
    guidelines = _guidelines(profile, sources)
    parts = []
    for code in profile.diagnoses:
        titles = [g.title for g in guidelines if icd10_matches(code, g.icd10)]
        parts.append(f"{code} ({'; '.join(titles)})" if titles else code)
    return _QUESTION.format(whom=_WHOM[profile.age_group], codes=", ".join(parts))


def context(profile: GuidanceProfile) -> str:
    """Сведения о пациенте для модели: только пол и возрастная группа."""
    sex = _SEX_TEXT.get(profile.sex or "", "не указан")
    return f"Пол: {sex}.\nВозраст: {_AGE_TEXT[profile.age_group]}."


def _guidelines(profile: GuidanceProfile, sources: Mapping[str, Source]) -> list[Source]:
    return [
        sources[i] for i in profile.source_ids if i in sources and sources[i].kind == _GUIDELINE
    ]


class GuidanceConflictError(Exception):
    """Повторить можно только расчёт со статусом `failed`."""


async def profile_of(
    session: AsyncSession, catalog: Catalog, patient: PatientSummary, today: date
) -> GuidanceProfile | None:
    """Профиль справки по текущим данным пациента."""
    return build_profile(
        catalog, patient, await patients.list_diagnoses(session, patient.id), today
    )


async def request(
    session: AsyncSession,
    patient_id: UUID,
    profile: GuidanceProfile,
    *,
    retry_failed: bool = False,
) -> None:
    """Поставить расчёт справки для текущего ключа пациента (спека #107, 4.4).

    Строка ключа уже есть: `pending` — задача ставится снова (дубля не даст `dedupe_key`),
    `done` — ничего. `failed` получает готовый ответ того же ключа, если он есть у любого
    пациента; иначе остаётся `failed` или, с `retry_failed`, снова идёт в расчёт.
    Строки нет: копия готового ответа того же ключа, пустой ответ без источников или новая
    строка `pending` с задачей.
    """
    row = await _row(session, patient_id, profile.key)
    if row is None:
        await _create(session, patient_id, profile)
        return
    if row.status == "failed":
        if await _copy_into(session, row) or not retry_failed:
            return
        row.status, row.failures = "pending", 0
        await session.flush()
    if row.status == "pending":
        await _enqueue(session, profile.key)


async def retry(session: AsyncSession, patient_id: UUID, profile: GuidanceProfile | None) -> None:
    """Расчёт текущего ключа из `failed` снова в расчёт.

    Raises:
        GuidanceConflictError: Нет диагнозов или расчёт не в статусе `failed`.
    """
    if profile is None:
        raise GuidanceConflictError
    row = await _row(session, patient_id, profile.key)
    if row is None or row.status != "failed":
        raise GuidanceConflictError
    await request(session, patient_id, profile, retry_failed=True)


async def view(
    session: AsyncSession, catalog: Catalog, patient_id: UUID, profile: GuidanceProfile | None
) -> GuidanceOut:
    """Ответ для врача: готовый по текущему ключу, иначе последний готовый по прежнему."""
    drugs = sorted(d.trade_name for d in catalog.drugs.values())
    if profile is None:
        return GuidanceOut(
            status=None,
            stale=False,
            claims=[],
            computed_at=None,
            guidelines=[],
            catalog_drugs=drugs,
            profile=None,
        )
    current = await _row(session, patient_id, profile.key)
    shown = current if current is not None and current.status == "done" else None
    if shown is None:
        shown = await session.scalar(
            select(Guidance)
            .where(Guidance.patient_id == patient_id, Guidance.status == "done")
            .order_by(Guidance.computed_at.desc())
            .limit(1)
        )
    answer = Answer.model_validate(shown.answer) if shown is not None else None
    return GuidanceOut(
        # Строки нет, только если её вставку перехватил параллельный запрос: он её и ставит.
        status=current.status if current is not None else "pending",
        stale=shown is not None and shown.profile_key != profile.key,
        claims=answer.claims if answer is not None else [],
        computed_at=shown.computed_at if shown is not None else None,
        guidelines=[
            GuidelineOut(id=g.id, title=g.title) for g in _guidelines(profile, catalog.sources)
        ],
        catalog_drugs=drugs,
        profile=_shown_profile(shown),
    )


def _shown_profile(row: Guidance | None) -> GuidanceProfileOut | None:
    """Профиль показанного ответа без источников; при `stale` — прежний, из его строки."""
    if row is None:
        return None
    profile = GuidanceProfile.from_json(row.profile)
    return GuidanceProfileOut(
        diagnoses=list(profile.diagnoses), sex=profile.sex, age_group=profile.age_group
    )


async def copy_ready(session: AsyncSession, key: str) -> bool:
    """Готовый ответ ключа — во все строки `pending` этого ключа. False — готового нет."""
    ready = await _ready(session, key)
    if ready is None:
        return False
    await session.execute(
        update(Guidance)
        .where(Guidance.profile_key == key, Guidance.status == "pending")
        .values(
            status="done", answer=ready.answer, run_id=ready.run_id, computed_at=ready.computed_at
        )
    )
    return True


async def pending_profile(session: AsyncSession, key: str) -> GuidanceProfile | None:
    """Профиль ключа, если хоть одна его строка ждёт расчёта."""
    data = await session.scalar(
        select(Guidance.profile)
        .where(Guidance.profile_key == key, Guidance.status == "pending")
        .limit(1)
    )
    return GuidanceProfile.from_json(data) if data is not None else None


async def save(session: AsyncSession, key: str, answer: Answer) -> None:
    """Ответ модели — во все строки `pending` ключа."""
    await session.execute(
        update(Guidance)
        .where(Guidance.profile_key == key, Guidance.status == "pending")
        .values(
            status="done",
            answer=answer.model_dump(mode="json"),
            run_id=answer.run_id,
            computed_at=func.now(),
        )
    )


async def record_failure(session: AsyncSession, key: str) -> list[int]:
    """Неудача задачи: счётчик строк `pending` +1, строки с `FAILURE_LIMIT` неудач — `failed`.

    Возвращает новые счётчики всех строк, которые ждали расчёта. Пустой список — таких строк
    не было.
    """
    counts = (
        await session.scalars(
            update(Guidance)
            .where(Guidance.profile_key == key, Guidance.status == "pending")
            .values(failures=Guidance.failures + 1)
            .returning(Guidance.failures)
        )
    ).all()
    await session.execute(
        update(Guidance)
        .where(
            Guidance.profile_key == key,
            Guidance.status == "pending",
            Guidance.failures >= FAILURE_LIMIT,
        )
        .values(status="failed")
    )
    return list(counts)


async def _create(session: AsyncSession, patient_id: UUID, profile: GuidanceProfile) -> None:
    key = profile.key
    values: dict[str, Any] = {
        "patient_id": patient_id,
        "profile_key": key,
        "profile": profile.to_json(),
    }
    ready = await _ready(session, key)
    if ready is not None:
        values |= {
            "status": "done",
            "answer": ready.answer,
            "run_id": ready.run_id,
            "computed_at": ready.computed_at,
        }
    elif not profile.source_ids:
        # По диагнозам нет КР, справочник пуст: искать не в чем, модель не нужна.
        empty = Answer(claims=[], dropped=0, run_id=None)
        values |= {
            "status": "done",
            "answer": empty.model_dump(mode="json"),
            "computed_at": func.now(),
        }
    else:
        values["status"] = "pending"
    # Параллельный запрос мог вставить строку раньше: тогда задачу ставит он.
    inserted = await session.scalar(
        insert(Guidance)
        .values(**values)
        .on_conflict_do_nothing(index_elements=["patient_id", "profile_key"])
        .returning(Guidance.id)
    )
    if inserted is not None and values["status"] == "pending":
        await _enqueue(session, key)


async def _copy_into(session: AsyncSession, row: Guidance) -> bool:
    """Готовый ответ того же ключа — в строку `row`. False — готового нет."""
    ready = await _ready(session, row.profile_key)
    if ready is None:
        return False
    row.status, row.answer, row.run_id = "done", ready.answer, ready.run_id
    row.computed_at = ready.computed_at
    await session.flush()
    return True


async def _row(session: AsyncSession, patient_id: UUID, key: str) -> Guidance | None:
    return await session.scalar(
        select(Guidance)
        .where(Guidance.patient_id == patient_id, Guidance.profile_key == key)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


async def _ready(session: AsyncSession, key: str) -> Guidance | None:
    return await session.scalar(
        select(Guidance)
        .where(Guidance.profile_key == key, Guidance.status == "done")
        .order_by(Guidance.computed_at.desc())
        .limit(1)
    )


async def _enqueue(session: AsyncSession, key: str) -> None:
    await enqueue(session, ENTRYPOINT, key.encode(), dedupe_key=f"{ENTRYPOINT}:{key}")
