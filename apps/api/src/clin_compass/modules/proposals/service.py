"""Публичный интерфейс модуля proposals: создание предложений, очередь врача и его решение."""

import base64
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from clin_compass.core.ai.runs import RunSummary, run_summaries
from clin_compass.modules.patients import service as patients
from clin_compass.modules.proposals.models import Proposal
from clin_compass.modules.proposals.schemas import (
    OriginType,
    ProposalOut,
    ProposalStatus,
    QueueItemOut,
    QueuePage,
    QueueSummary,
    RunInfo,
)

Applier = Callable[[AsyncSession, ProposalOut], Awaitable[None]]


class ApplierRegistry:
    """Какой модуль применяет подтверждённое предложение данного вида.

    Модули регистрируют свои appliers при сборке приложения (main.create_app).
    """

    def __init__(self) -> None:
        self._appliers: dict[str, Applier] = {}
        self._in_module: set[str] = set()

    def register(self, kind: str, applier: Applier, *, decided_in_module: bool = False) -> None:
        """`decided_in_module` — решение принимает своя ручка модуля (направление, #35):
        общие `confirm` и `reject` такой вид не решают."""
        if kind in self._appliers:
            raise ValueError(f"Applier for {kind!r} is already registered")
        self._appliers[kind] = applier
        if decided_in_module:
            self._in_module.add(kind)

    def decided_in_module(self, kind: str) -> bool:
        return kind in self._in_module

    def get(self, kind: str) -> Applier | None:
        return self._appliers.get(kind)


class ProposalNotFoundError(Exception):
    """Предложения нет, или запрашивающий не ведущий врач пациента."""


class ProposalAlreadyDecidedError(Exception):
    pass


class DecidedInModuleError(Exception):
    """Вид решается своей ручкой модуля, а не общими `confirm` и `reject`."""


class NoApplierError(Exception):
    """Для вида предложения не зарегистрирован applier: подтвердить нечем."""


class ProposalApplyError(Exception):
    """Applier отказался применять предложение. Текст показывается врачу."""


class InvalidCursorError(Exception):
    """Курсор страницы очереди не разобрать."""


# Действия самого врача (отмена назначения, подтверждение с изменённой схемой) решать
# в очереди нечего: ни в списке, ни в счётчиках их нет.
_FROM_SYSTEM = Proposal.origin_type != "doctor"


async def create_proposal(
    session: AsyncSession,
    *,
    patient_id: UUID,
    kind: str,
    payload: dict[str, Any],
    origin_type: OriginType,
    origin_ref: str,
) -> ProposalOut:
    proposal = Proposal(
        patient_id=patient_id,
        kind=kind,
        payload=payload,
        origin_type=origin_type,
        origin_ref=origin_ref,
    )
    session.add(proposal)
    await session.flush()
    await session.refresh(proposal)
    return ProposalOut.model_validate(proposal)


async def list_for_patient(session: AsyncSession, *, patient_id: UUID) -> list[ProposalOut]:
    """Предложения пациента. Право доступа проверяет вызывающий (patients.deps.owned_patient)."""
    rows = await session.scalars(
        select(Proposal).where(Proposal.patient_id == patient_id).order_by(Proposal.created_at)
    )
    return [ProposalOut.model_validate(p) for p in rows]


async def list_for_doctor(
    session: AsyncSession,
    *,
    doctor_id: UUID,
    status: ProposalStatus,
    q: str | None,
    cursor: str | None,
    limit: int,
) -> QueuePage:
    """Предложения всех пациентов врача, новые выше (спека #96, раздел 3.1).

    Пациентов и ФИО даёт `patients.list_patients`: чужие предложения сюда не попадают.
    Предложений из действий самого врача (`origin_type = "doctor"`) в очереди нет.
    `q` — часть ФИО без учёта регистра, «ё» равна «е». Порядок — `created_at` и `id`
    по убыванию, курсор — эта пара у последнего предложения страницы.

    Raises:
        InvalidCursorError: Курсор не выдан этой функцией.
    """
    after = _decode_cursor(cursor) if cursor else None
    needle = _searchable(q or "")
    names = {
        p.id: p.full_name
        for p in await patients.list_patients(session, doctor_id)
        if needle in _searchable(p.full_name)
    }
    if not names:
        return QueuePage(items=[], next_cursor=None)
    query = (
        select(Proposal)
        .where(Proposal.patient_id.in_(names), Proposal.status == status, _FROM_SYSTEM)
        .order_by(Proposal.created_at.desc(), Proposal.id.desc())
        .limit(limit + 1)
    )
    if after is not None:
        created, last_id = after
        query = query.where(
            or_(
                Proposal.created_at < created,
                and_(Proposal.created_at == created, Proposal.id < last_id),
            )
        )
    rows = list(await session.scalars(query))
    found = [ProposalOut.model_validate(p) for p in rows[:limit]]
    runs = await run_summaries(session, {r for p in found if (r := _run_id(p)) is not None})
    items = [
        QueueItemOut(**p.model_dump(), patient_name=names[p.patient_id], run=_run(p, runs))
        for p in found
    ]
    last = found[-1] if len(rows) > limit else None
    return QueuePage(
        items=items,
        next_cursor=None if last is None else _encode_cursor(last.created_at, last.id),
    )


async def summary_for_doctor(
    session: AsyncSession, *, doctor_id: UUID, since: datetime
) -> QueueSummary:
    """Сколько предложений пациентов врача ждут решения и сколько решено с `since`.

    Считаются те же предложения, что показывает `list_for_doctor`.
    """
    patient_ids = [p.id for p in await patients.list_patients(session, doctor_id)]
    if not patient_ids:
        return QueueSummary(pending=0, decided_today=0)
    pending, decided = (
        await session.execute(
            select(
                func.count().filter(Proposal.status == "proposed"),
                func.count().filter(Proposal.decided_at >= since),
            ).where(Proposal.patient_id.in_(patient_ids), _FROM_SYSTEM)
        )
    ).one()
    return QueueSummary(pending=pending, decided_today=decided)


def _searchable(name: str) -> str:
    """Для поиска по ФИО: без учёта регистра и пробелов по краям, «ё» как «е»."""
    return name.strip().casefold().replace("ё", "е")


def _run_id(proposal: ProposalOut) -> UUID | None:
    """Id строки `ai_runs` у предложения из AI-вызова; у остальных и у кривой ссылки — None."""
    if proposal.origin_type != "ai_run":
        return None
    try:
        return UUID(proposal.origin_ref)
    except ValueError:
        return None


def _run(proposal: ProposalOut, runs: dict[UUID, RunSummary]) -> RunInfo | None:
    run_id = _run_id(proposal)
    summary = runs.get(run_id) if run_id is not None else None
    return RunInfo.model_validate(summary) if summary is not None else None


def _encode_cursor(created_at: datetime, proposal_id: UUID) -> str:
    raw = f"{created_at.isoformat()}|{proposal_id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    # Ошибки base64, UTF-8, ISO-даты и UUID — подклассы ValueError.
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        created, proposal_id = raw.split("|")
        after = datetime.fromisoformat(created), UUID(proposal_id)
    except ValueError:
        raise InvalidCursorError from None
    if after[0].tzinfo is None:
        raise InvalidCursorError
    return after


async def reject(
    session: AsyncSession, *, doctor_id: UUID, proposal_id: UUID, comment: str | None
) -> ProposalOut:
    """Отклонить предложение. Applier для отклонения не нужен."""
    return await decide(
        session,
        ApplierRegistry(),
        doctor_id=doctor_id,
        proposal_id=proposal_id,
        confirm=False,
        comment=comment,
    )


async def get_proposal(session: AsyncSession, proposal_id: UUID) -> ProposalOut | None:
    """Предложение по id. Право доступа проверяет вызывающий."""
    proposal = await session.get(Proposal, proposal_id)
    return ProposalOut.model_validate(proposal) if proposal is not None else None


async def list_open(session: AsyncSession, *, patient_id: UUID, kind: str) -> list[ProposalOut]:
    """Нерешённые предложения пациента одного вида, старые первыми."""
    rows = await session.scalars(
        select(Proposal)
        .where(
            Proposal.patient_id == patient_id,
            Proposal.kind == kind,
            Proposal.status == "proposed",
        )
        .order_by(Proposal.created_at)
    )
    return [ProposalOut.model_validate(p) for p in rows]


async def decide(
    session: AsyncSession,
    registry: ApplierRegistry,
    *,
    doctor_id: UUID,
    proposal_id: UUID,
    confirm: bool,
    comment: str | None,
    common_route: bool = False,
) -> ProposalOut:
    """Решение врача. При подтверждении applier работает в той же транзакции.

    Коммит — у вызывающего: если applier упал, откат вернёт предложение в `proposed`.
    `common_route` — вызов из общих ручек: вид, который решается в своём модуле, —
    `DecidedInModuleError`.
    """
    proposal = await session.scalar(
        select(Proposal).where(Proposal.id == proposal_id).with_for_update()
    )
    if proposal is None or await patients.lead_doctor_of(session, proposal.patient_id) != doctor_id:
        raise ProposalNotFoundError
    if proposal.status != "proposed":
        raise ProposalAlreadyDecidedError
    if common_route and registry.decided_in_module(proposal.kind):
        raise DecidedInModuleError

    proposal.status = "confirmed" if confirm else "rejected"
    proposal.decided_by = doctor_id
    proposal.decided_at = datetime.now(UTC)
    proposal.decision_comment = comment
    decided = ProposalOut.model_validate(proposal)
    if confirm:
        applier = registry.get(proposal.kind)
        if applier is None:
            raise NoApplierError
        await applier(session, decided)
    await session.flush()
    return decided
