"""Публичный интерфейс модуля summary: сводка динамики пациента между визитами (спека #38).

Период начинается с подтверждения прошлой сводки, первый — с регистрации пациента. Факты
периода собирает `facts.collect` из данных модулей `assessments`, `alerts` и `freetext`.
Ключ данных — пациент, начало периода и факты: данные поменялись — новая строка и новый
расчёт в worker'е (`tasks.py`), прежний черновик показывается устаревшим. Доступ проверяет
вызывающий (`patients.deps.owned_patient`). Коммитит вызывающий.
"""

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, cast
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from clin_compass.core.queue import enqueue
from clin_compass.modules.alerts import service as alerts
from clin_compass.modules.assessments import service as assessments
from clin_compass.modules.freetext import service as freetext
from clin_compass.modules.identity import service as identity
from clin_compass.modules.patients import service as patients
from clin_compass.modules.patients.schemas import PatientSummary
from clin_compass.modules.summary.facts import collect
from clin_compass.modules.summary.models import VisitSummary
from clin_compass.modules.summary.schemas import (
    SummaryFact,
    SummaryStatement,
    SummaryStatus,
    VisitSummaryOut,
)

ENTRYPOINT = "summary.draft"
STALE = "Данные изменились, обновите сводку"
ALREADY_CONFIRMED = "Сводка уже подтверждена"
NOT_FAILED = "Повторить можно только сводку, которую составить не удалось"
UNKNOWN_FACT = "Номер факта не из черновика"
TOO_MANY_STATEMENTS = "Утверждений больше, чем в черновике"
ConflictCode = Literal["already_confirmed", "stale", "not_failed"]
VersionCode = Literal["unknown_fact", "too_many_statements"]


class SummaryNotFoundError(Exception):
    """Сводки нет или она другого пациента."""


class SummaryConflictError(Exception):
    """Действие невозможно в текущем состоянии сводки: `code` и текст для врача."""

    def __init__(self, code: ConflictCode, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


class InvalidVersionError(Exception):
    """Версия врача добавляет к черновику: утверждение или факт, на который он не ссылается.

    Врач правит текст и убирает утверждения (спека #38, 3.5). Новое утверждение нельзя
    проверить по фактам.
    """

    def __init__(self, code: VersionCode, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Period:
    """Текущий период пациента: начало, факты и ключ данных."""

    start: datetime
    facts: tuple[SummaryFact, ...]
    key: str


def data_key(patient_id: UUID, start: datetime, facts: Sequence[SummaryFact]) -> str:
    """SHA-256 от пациента, начала периода и фактов: 64 знака."""
    raw = json.dumps(
        {
            "patient_id": str(patient_id),
            "period_start": start.isoformat(),
            "facts": [f.model_dump(mode="json") for f in facts],
        },
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode()).hexdigest()


async def current_period(session: AsyncSession, patient: PatientSummary, zone: ZoneInfo) -> Period:
    """Период с прошлого подтверждения (или с регистрации) и его факты на сейчас."""
    confirmed = await _last_confirmed(session, patient.id)
    # У подтверждённой строки `confirmed_at` всегда есть (ограничение `confirmed_complete`).
    start = (confirmed.confirmed_at if confirmed is not None else None) or patient.created_at
    findings = await freetext.list_confirmed(session, patient_id=patient.id, since=start)
    facts = collect(
        start=start,
        results=await assessments.list_all_for_owned_patient(session, patient),
        alerts=await alerts.list_for_patient(session, patient_id=patient.id),
        findings=findings,
        written=await freetext.written_at(session, [f.analysis_id for f in findings]),
        zone=zone,
    )
    return Period(start, tuple(facts), data_key(patient.id, start, facts))


async def current_key(session: AsyncSession, patient_id: UUID, zone: ZoneInfo) -> str | None:
    """Ключ данных пациента на сейчас: по нему worker узнаёт, не устарела ли задача.

    Пациента нет — None.
    """
    doctor_id = await patients.lead_doctor_of(session, patient_id)
    patient = (
        await patients.get_patient_for_doctor(session, doctor_id=doctor_id, patient_id=patient_id)
        if doctor_id is not None
        else None
    )
    if patient is None:
        return None
    return (await current_period(session, patient, zone)).key


async def get_summary(
    session: AsyncSession, patient: PatientSummary, zone: ZoneInfo
) -> VisitSummaryOut:
    """Сводка для врача; черновика по текущему ключу нет — ставит расчёт (спека #38, 3.5).

    Фактов за период нет: последняя подтверждённая сводка, а до первого подтверждения —
    `empty` без вызова модели.
    """
    period = await current_period(session, patient, zone)
    if not period.facts:
        confirmed = await _last_confirmed(session, patient.id)
        if confirmed is not None:
            return await _out(session, confirmed, status="confirmed", stale=False)
        return _empty(period.start)
    row = await _request(session, patient.id, period)
    return await _view(session, row, period)


async def confirm(
    session: AsyncSession,
    patient: PatientSummary,
    zone: ZoneInfo,
    *,
    summary_id: UUID,
    doctor_id: UUID,
    statements: Sequence[SummaryStatement],
) -> VisitSummaryOut:
    """Подтвердить черновик версией врача. Подтверждение закрывает период.

    Raises:
        SummaryNotFoundError: Сводки нет у этого пациента.
        SummaryConflictError: `already_confirmed` — уже подтверждена; `stale` — черновика
            нет или данные периода изменились.
        InvalidVersionError: `too_many_statements` — утверждений больше, чем в черновике;
            `unknown_fact` — номер факта, на который не ссылается черновик.
    """
    row = await session.scalar(
        select(VisitSummary)
        .where(VisitSummary.id == summary_id, VisitSummary.patient_id == patient.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise SummaryNotFoundError
    if row.status == "confirmed":
        raise SummaryConflictError("already_confirmed", ALREADY_CONFIRMED)
    period = await current_period(session, patient, zone)
    if row.status != "done" or row.data_key != period.key:
        raise SummaryConflictError("stale", STALE)
    draft = row.draft or []
    if len(statements) > len(draft):
        raise InvalidVersionError("too_many_statements", TOO_MANY_STATEMENTS)
    cited = {n for statement in draft for n in statement["facts"]}
    if any(n not in cited for s in statements for n in s.facts):
        raise InvalidVersionError("unknown_fact", UNKNOWN_FACT)
    row.status = "confirmed"
    row.confirmed_statements = [s.model_dump(mode="json") for s in statements]
    row.confirmed_by = doctor_id
    row.confirmed_at = func.now()
    try:
        await session.flush()
    except IntegrityError:
        # Другую сводку этого периода подтвердили параллельно: период уже закрыт.
        raise SummaryConflictError("stale", STALE) from None
    await session.refresh(row)
    return await _out(session, row, status="confirmed", stale=False)


async def retry(session: AsyncSession, patient: PatientSummary, zone: ZoneInfo) -> VisitSummaryOut:
    """Сводку текущего ключа, которую составить не удалось, — снова в расчёт.

    Raises:
        SummaryConflictError: `not_failed` — фактов нет или расчёт не в статусе `failed`.
    """
    period = await current_period(session, patient, zone)
    row = await _by_key(session, period.key) if period.facts else None
    if row is None or row.status != "failed":
        raise SummaryConflictError("not_failed", NOT_FAILED)
    row.status, row.failures = "pending", 0
    await session.flush()
    await _enqueue(session, period.key)
    return await _view(session, row, period)


async def _request(session: AsyncSession, patient_id: UUID, period: Period) -> VisitSummary:
    """Строка текущего ключа: новая — `pending` с задачей; `pending` — задача ещё раз.

    Дубля задачи не даст `dedupe_key`. Строку, которую параллельно вставил другой запрос,
    Postgres отдаёт после его коммита.
    """
    row = await _by_key(session, period.key)
    if row is None:
        inserted = await session.scalar(
            insert(VisitSummary)
            .values(
                patient_id=patient_id,
                period_start=period.start,
                period_end=func.now(),
                data_key=period.key,
                status="pending",
                facts=[f.model_dump(mode="json") for f in period.facts],
            )
            .on_conflict_do_nothing(index_elements=["data_key"])
            .returning(VisitSummary.id)
        )
        if inserted is not None:
            await _enqueue(session, period.key)
        row = await _by_key(session, period.key)
        if row is None:
            raise RuntimeError("summary row disappeared after insert")
    elif row.status == "pending":
        await _enqueue(session, period.key)
    return row


async def _view(session: AsyncSession, row: VisitSummary, period: Period) -> VisitSummaryOut:
    """Готовая строка ключа или, пока она считается или упала, прежний черновик периода."""
    if row.status in ("done", "confirmed"):
        return await _out(session, row, status=cast(SummaryStatus, row.status), stale=False)
    previous = await session.scalar(
        select(VisitSummary)
        .where(
            VisitSummary.patient_id == row.patient_id,
            VisitSummary.period_start == period.start,
            VisitSummary.status == "done",
            VisitSummary.id != row.id,
        )
        .order_by(VisitSummary.computed_at.desc())
        .limit(1)
    )
    status: SummaryStatus = "failed" if row.status == "failed" else "pending"
    if previous is None:
        return await _out(session, row, status=status, stale=False)
    return await _out(session, previous, status=status, stale=True)


async def _out(
    session: AsyncSession, row: VisitSummary, *, status: SummaryStatus, stale: bool
) -> VisitSummaryOut:
    confirmed = row.status == "confirmed"
    statements = (row.confirmed_statements if confirmed else row.draft) or []
    doctor = await identity.get_user(session, row.confirmed_by) if row.confirmed_by else None
    return VisitSummaryOut(
        id=row.id,
        status=status,
        period_start=row.period_start,
        period_end=row.period_end,
        stale=stale,
        facts=[SummaryFact.model_validate(f) for f in row.facts],
        statements=[SummaryStatement.model_validate(s) for s in statements],
        edited=confirmed and row.confirmed_statements != row.draft,
        confirmed_by=row.confirmed_by,
        confirmed_by_name=doctor.full_name if doctor is not None else None,
        confirmed_at=row.confirmed_at,
    )


def _empty(start: datetime) -> VisitSummaryOut:
    return VisitSummaryOut(
        id=None,
        status="empty",
        period_start=start,
        period_end=None,
        stale=False,
        facts=[],
        statements=[],
        edited=False,
        confirmed_by=None,
        confirmed_by_name=None,
        confirmed_at=None,
    )


async def _last_confirmed(session: AsyncSession, patient_id: UUID) -> VisitSummary | None:
    return await session.scalar(
        select(VisitSummary)
        .where(VisitSummary.patient_id == patient_id, VisitSummary.status == "confirmed")
        .order_by(VisitSummary.confirmed_at.desc())
        .limit(1)
    )


async def _by_key(session: AsyncSession, key: str) -> VisitSummary | None:
    return await session.scalar(
        select(VisitSummary)
        .where(VisitSummary.data_key == key)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


async def _enqueue(session: AsyncSession, key: str) -> None:
    await enqueue(session, ENTRYPOINT, key.encode(), dedupe_key=f"{ENTRYPOINT}:{key}")
