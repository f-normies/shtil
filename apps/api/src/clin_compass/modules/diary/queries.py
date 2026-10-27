"""Чтения дневника пациента и врача с пакетным состоянием источников."""

import base64
import json
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from clin_compass.modules.diary import access
from clin_compass.modules.diary.errors import DiaryError
from clin_compass.modules.diary.models import Audio, Entry, Revision
from clin_compass.modules.diary.schemas import (
    DiaryAudioOut,
    DiaryContextOut,
    DiaryEntryOut,
    DiaryEntryPage,
    DiaryFilter,
    DiarySourceStatusOut,
)
from clin_compass.modules.freetext import service as freetext
from clin_compass.modules.identity.schemas import UserOut
from clin_compass.modules.patients import service as patients


async def context(
    session: AsyncSession,
    actor: UserOut,
    *,
    timezone: ZoneInfo,
) -> DiaryContextOut:
    """Контекст календарного дня, ежедневной отметки и бусин текущего месяца."""
    await access.authorize_self_patient(session, actor, action="diary.context.read")
    tz = timezone
    now = datetime.now(UTC)
    today = now.astimezone(tz).date()
    month_start = today.replace(day=1)
    next_month = date(today.year + (today.month == 12), today.month % 12 + 1, 1)
    start_at = datetime.combine(month_start, time.min, tz).astimezone(UTC)
    end_at = datetime.combine(next_month, time.min, tz).astimezone(UTC)
    count = len(
        list(
            await session.scalars(
                select(Entry.id).where(
                    Entry.patient_id == actor.id,
                    Entry.hidden_at.is_(None),
                    Entry.occurred_at >= start_at,
                    Entry.occurred_at < end_at,
                )
            )
        )
    )
    saved = await session.scalar(
        select(Entry.id).where(
            Entry.patient_id == actor.id,
            Entry.kind == "daily",
            Entry.daily_on == today,
            Entry.hidden_at.is_(None),
        )
    )
    observation = await patients.observation_start(session, actor.id)
    return DiaryContextOut(
        server_now=now,
        today=today,
        timezone=timezone.key,
        observation_from=observation.astimezone(tz).date(),
        daily_status="saved" if saved is not None else "missing",
        month_entry_count=count,
    )


async def get_patient_entry(
    session: AsyncSession,
    actor: UserOut,
    entry_id: UUID,
) -> DiaryEntryOut:
    """Прочитать текущую запись пациента без врачебных находок."""
    entry, _doctor_id = await access.authorize_patient_entry(
        session,
        actor,
        entry_id=entry_id,
        action="diary.entry.read",
    )
    if entry is None or entry.patient_id != actor.id or entry.hidden_at is not None:
        raise DiaryError(404, "not_found", "Diary entry not found")
    return await _patient_out(session, entry)


async def list_patient_entries(
    session: AsyncSession,
    actor: UserOut,
    *,
    timezone: ZoneInfo,
    filter_: DiaryFilter = "all",
    start: date | None = None,
    end: date | None = None,
    cursor: str | None = None,
    limit: int = 20,
) -> DiaryEntryPage:
    """Постранично прочитать видимые записи пациента; курсор связан с фильтром."""
    await access.authorize_self_patient(session, actor, action="diary.entries.list")
    lower, upper = _date_bounds(start, end, timezone)
    statement = select(Entry).where(
        Entry.patient_id == actor.id,
        Entry.hidden_at.is_(None),
    )
    if lower is not None:
        statement = statement.where(Entry.occurred_at >= lower)
    if upper is not None:
        statement = statement.where(Entry.occurred_at < upper)
    if filter_ == "mood":
        statement = statement.join(
            Revision,
            and_(Revision.entry_id == Entry.id, Revision.revision == Entry.current_revision),
        ).where(Revision.mood_level.is_not(None))
    elif filter_ == "records":
        statement = statement.where(Entry.kind != "mood")
    if cursor is not None:
        occurred_at, entry_id = _decode_cursor(
            cursor,
            patient_id=actor.id,
            start=start,
            end=end,
            filter_=filter_,
        )
        statement = statement.where(
            or_(
                Entry.occurred_at < occurred_at,
                and_(Entry.occurred_at == occurred_at, Entry.id < entry_id),
            )
        )
    rows = list(
        await session.scalars(
            statement.order_by(Entry.occurred_at.desc(), Entry.id.desc()).limit(limit + 1)
        )
    )
    page = rows[:limit]
    next_cursor = (
        _encode_cursor(page[-1], patient_id=actor.id, start=start, end=end, filter_=filter_)
        if len(rows) > limit and page
        else None
    )
    return DiaryEntryPage(
        items=[await _patient_out(session, entry) for entry in page],
        next_cursor=next_cursor,
    )


async def _patient_out(session: AsyncSession, entry: Entry) -> DiaryEntryOut:
    revision = await _revision(session, entry, entry.current_revision)
    audio_row = await session.get(Audio, revision.audio_id) if revision.audio_id else None
    source_pairs = _source_pairs(revision)
    statuses = await freetext.statuses(
        session,
        source_type="diary",
        source_ids=[source_id for _, source_id in source_pairs],
    )
    return _patient_entry_out(entry, revision, audio_row, statuses)


def _patient_entry_out(
    entry: Entry,
    revision: Revision,
    audio_row: Audio | None,
    statuses: dict[UUID, Any],
) -> DiaryEntryOut:
    """Собрать пациентский контракт из заранее прочитанных версии, аудио и статусов."""
    source_pairs = _source_pairs(revision)
    source_statuses = [
        DiarySourceStatusOut(field=field, source_id=source_id, status=statuses.get(source_id))
        for field, source_id in source_pairs
    ]
    if audio_row is not None and audio_row.transcript_source_id is not None:
        transcript_status = statuses.get(audio_row.transcript_source_id)
        source_statuses.append(
            DiarySourceStatusOut(
                field="transcript",
                source_id=audio_row.transcript_source_id,
                status=transcript_status,
            )
        )
    return DiaryEntryOut(
        entry_id=entry.id,
        kind=entry.kind,
        occurred_at=entry.occurred_at,
        daily_on=entry.daily_on,
        revision=entry.current_revision,
        received_at=entry.received_at,
        mood_level=revision.mood_level,
        mood_words=revision.mood_words,
        text=revision.text,
        other_medicine_text=revision.other_medicine_text,
        audio=_audio_out(audio_row),
        source_statuses=source_statuses,
    )


async def _revision(session: AsyncSession, entry: Entry, number: int) -> Revision:
    revision = await session.scalar(
        select(Revision).where(Revision.entry_id == entry.id, Revision.revision == number)
    )
    if revision is None:
        raise RuntimeError("diary entry has no requested revision")
    return revision


def _source_pairs(revision: Revision) -> list[tuple[str, UUID]]:
    pairs = []
    if revision.text_source_id is not None:
        pairs.append(("text", revision.text_source_id))
    if revision.other_medicine_source_id is not None:
        pairs.append(("other_medicine", revision.other_medicine_source_id))
    return pairs


def _audio_out(audio: Audio | None) -> DiaryAudioOut | None:
    if audio is None:
        return None
    return DiaryAudioOut(
        id=audio.id,
        status=audio.status,
        duration_ms=audio.duration_ms,
        transcript=audio.transcript,
        transcript_source_id=audio.transcript_source_id,
    )


def _date_bounds(
    start: date | None, end: date | None, timezone: ZoneInfo
) -> tuple[datetime | None, datetime | None]:
    if (start is None) != (end is None):
        raise DiaryError(422, "invalid_period", "Both start and end are required")
    if start is None or end is None:
        return None, None
    tz = timezone
    return (
        datetime.combine(start, time.min, tz).astimezone(UTC),
        datetime.combine(end + timedelta(days=1), time.min, tz).astimezone(UTC),
    )


def _cursor_binding(
    patient_id: UUID, start: date | None, end: date | None, filter_: str
) -> dict[str, Any]:
    return {
        "patient_id": str(patient_id),
        "start": start.isoformat() if start else None,
        "end": end.isoformat() if end else None,
        "filter": filter_,
    }


def _encode_cursor(
    entry: Entry,
    *,
    patient_id: UUID,
    start: date | None,
    end: date | None,
    filter_: str,
) -> str:
    payload = {
        **_cursor_binding(patient_id, start, end, filter_),
        "occurred_at": entry.occurred_at.astimezone(UTC).isoformat(),
        "entry_id": str(entry.id),
    }
    return base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()


def _decode_cursor(
    cursor: str,
    *,
    patient_id: UUID,
    start: date | None,
    end: date | None,
    filter_: str,
) -> tuple[datetime, UUID]:
    try:
        payload = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        binding = _cursor_binding(patient_id, start, end, filter_)
        if any(payload.get(key) != value for key, value in binding.items()):
            raise ValueError("cursor does not match query")
        occurred_at = datetime.fromisoformat(payload["occurred_at"])
        entry_id = UUID(payload["entry_id"])
        if occurred_at.tzinfo is None:
            raise ValueError("cursor timestamp must include timezone")
        return occurred_at.astimezone(UTC), entry_id
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise DiaryError(422, "invalid_cursor", "Cursor is invalid for this query") from error
