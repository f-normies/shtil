"""Чтения дневника врача и пакетная выдача находок по странице."""

from collections import defaultdict
from datetime import UTC, date, datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from clin_compass.modules.diary import access, queries
from clin_compass.modules.diary.errors import DiaryError
from clin_compass.modules.diary.models import Audio, Entry, Revision
from clin_compass.modules.diary.schemas import (
    DiaryDoctorEntryOut,
    DiaryDoctorEntryPage,
    DiaryDoctorSourceOut,
    DiaryFilter,
    DiaryMoodPoint,
    DiaryMoodPointsOut,
)
from clin_compass.modules.freetext import service as freetext
from clin_compass.modules.patients import service as patients


async def list_doctor_entries(
    session: AsyncSession,
    *,
    doctor_id: UUID,
    patient_id: UUID,
    start: date,
    end: date,
    timezone: ZoneInfo,
    filter_: DiaryFilter = "all",
    cursor: str | None = None,
    limit: int = 20,
) -> DiaryDoctorEntryPage:
    """Прочитать страницу и пакетно присоединить статусы и находки источников."""
    await access.authorize_doctor(
        session,
        doctor_id=doctor_id,
        patient_id=patient_id,
        action="diary.patient_feed.list",
    )
    await validate_period(session, patient_id=patient_id, start=start, end=end, timezone=timezone)
    lower, upper = queries._date_bounds(start, end, timezone)
    statement = select(Entry).where(
        Entry.patient_id == patient_id,
        Entry.hidden_at.is_(None),
        Entry.occurred_at >= lower,
        Entry.occurred_at < upper,
    )
    if filter_ == "mood":
        statement = statement.join(
            Revision,
            and_(Revision.entry_id == Entry.id, Revision.revision == Entry.current_revision),
        ).where(Revision.mood_level.is_not(None))
    elif filter_ == "records":
        statement = statement.where(Entry.kind != "mood")
    if cursor is not None:
        occurred_at, entry_id = queries._decode_cursor(
            cursor,
            patient_id=patient_id,
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
        queries._encode_cursor(
            page[-1], patient_id=patient_id, start=start, end=end, filter_=filter_
        )
        if len(rows) > limit and page
        else None
    )
    if not page:
        return DiaryDoctorEntryPage(items=[], next_cursor=next_cursor)

    entry_ids = [entry.id for entry in page]
    revisions = list(
        await session.scalars(
            select(Revision)
            .where(Revision.entry_id.in_(entry_ids))
            .order_by(Revision.entry_id, Revision.revision)
        )
    )
    revisions_by_entry: dict[UUID, list[Revision]] = defaultdict(list)
    for revision in revisions:
        revisions_by_entry[revision.entry_id].append(revision)

    audio_ids = {revision.audio_id for revision in revisions if revision.audio_id is not None}
    audio_rows = (
        list(await session.scalars(select(Audio).where(Audio.id.in_(audio_ids))))
        if audio_ids
        else []
    )
    audio_by_id = {audio.id: audio for audio in audio_rows}
    sources_by_entry: dict[UUID, dict[tuple[str, UUID], int]] = {}
    current_source_ids: set[UUID] = set()
    for entry in page:
        entry_revisions = revisions_by_entry.get(entry.id, [])
        current = next(
            (
                revision
                for revision in entry_revisions
                if revision.revision == entry.current_revision
            ),
            None,
        )
        if current is None:
            raise RuntimeError("diary entry has no current revision")
        current_source_ids.update(source_id for _, source_id in queries._source_pairs(current))
        source_revision: dict[tuple[str, UUID], int] = {}
        for revision in entry_revisions:
            for field, source_id in queries._source_pairs(revision):
                source_revision.setdefault((field, source_id), revision.revision)
            audio = audio_by_id.get(revision.audio_id) if revision.audio_id else None
            if audio is not None and audio.transcript_source_id is not None:
                source_revision.setdefault(
                    ("transcript", audio.transcript_source_id), revision.revision
                )
        current_audio = audio_by_id.get(current.audio_id) if current.audio_id else None
        if current_audio is not None and current_audio.transcript_source_id is not None:
            current_source_ids.add(current_audio.transcript_source_id)
        sources_by_entry[entry.id] = source_revision

    statuses = (
        await freetext.statuses(session, source_type="diary", source_ids=current_source_ids)
        if current_source_ids
        else {}
    )
    all_source_ids = {
        source_id for sources in sources_by_entry.values() for _, source_id in sources
    }
    states = (
        await freetext.list_diary_sources(session, patient_id=patient_id, source_ids=all_source_ids)
        if all_source_ids
        else {}
    )
    items: list[DiaryDoctorEntryOut] = []
    for entry in page:
        entry_revisions = revisions_by_entry[entry.id]
        current = next(
            revision for revision in entry_revisions if revision.revision == entry.current_revision
        )
        base = queries._patient_entry_out(
            entry,
            current,
            audio_by_id.get(current.audio_id) if current.audio_id else None,
            statuses,
        )
        sources = [
            DiaryDoctorSourceOut(
                field=field,
                source_id=source_id,
                revision=revision_number,
                status=states[source_id].status if source_id in states else None,
                proposals=states[source_id].proposals if source_id in states else [],
                findings=states[source_id].findings if source_id in states else [],
            )
            for (field, source_id), revision_number in sources_by_entry[entry.id].items()
        ]
        items.append(DiaryDoctorEntryOut(**base.model_dump(), sources=sources))
    return DiaryDoctorEntryPage(items=items, next_cursor=next_cursor)


async def doctor_moods(
    session: AsyncSession,
    *,
    doctor_id: UUID,
    patient_id: UUID,
    start: date,
    end: date,
    timezone: ZoneInfo,
) -> DiaryMoodPointsOut:
    """Вернуть все настроения периода независимо от размера страницы дневника."""
    await access.authorize_doctor(
        session,
        doctor_id=doctor_id,
        patient_id=patient_id,
        action="diary.patient_moods.list",
    )
    await validate_period(session, patient_id=patient_id, start=start, end=end, timezone=timezone)
    lower, upper = queries._date_bounds(start, end, timezone)
    rows = await session.execute(
        select(Entry, Revision)
        .join(
            Revision,
            and_(Revision.entry_id == Entry.id, Revision.revision == Entry.current_revision),
        )
        .where(
            Entry.patient_id == patient_id,
            Entry.hidden_at.is_(None),
            Entry.occurred_at >= lower,
            Entry.occurred_at < upper,
            Revision.mood_level.is_not(None),
        )
        .order_by(Entry.occurred_at.desc(), Entry.id.desc())
    )
    return DiaryMoodPointsOut(
        items=[
            DiaryMoodPoint(
                entry_id=entry.id,
                occurred_at=entry.occurred_at,
                daily_on=entry.daily_on,
                mood_level=revision.mood_level,
                mood_words=revision.mood_words,
            )
            for entry, revision in rows
        ]
    )


async def validate_period(
    session: AsyncSession,
    *,
    patient_id: UUID,
    start: date,
    end: date,
    timezone: ZoneInfo,
) -> None:
    """Проверить inclusive диапазон 7–90 дней в календаре системы и наблюдения."""
    tz = timezone
    today = datetime.now(UTC).astimezone(tz).date()
    observation_from = (await patients.observation_start(session, patient_id)).astimezone(tz).date()
    days = (end - start).days + 1
    if days < 7 or days > 90 or start < observation_from or end > today:
        raise DiaryError(
            422, "invalid_period", "Period must be 7–90 days within observation and today"
        )
