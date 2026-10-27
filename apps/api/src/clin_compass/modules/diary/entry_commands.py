"""Транзакционные команды записей дневника."""

from datetime import UTC, datetime
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from clin_compass.core import queue, realtime
from clin_compass.modules.diary import access
from clin_compass.modules.diary.errors import DiaryError
from clin_compass.modules.diary.intake_snapshot import snapshot_daily_intake
from clin_compass.modules.diary.models import Audio, Entry, Revision, RevisionIntakeMark
from clin_compass.modules.diary.operations import (
    canonical_command_hash,
    lock_operation_key,
    replay_receipt,
    save_receipt,
)
from clin_compass.modules.diary.schemas import (
    DiaryAudioCreateIn,
    DiaryCommandReceipt,
    DiaryEntryCreateIn,
    DiaryEntryPatchIn,
    DiaryVoiceEntryCreateIn,
)
from clin_compass.modules.freetext import service as freetext
from clin_compass.modules.identity.schemas import UserOut


async def create_entry(
    session: AsyncSession,
    actor: UserOut,
    body: DiaryEntryCreateIn,
    *,
    timezone: ZoneInfo,
) -> tuple[DiaryCommandReceipt, bool]:
    """Создать запись и поставить каждый непустой текст на разбор в одной транзакции."""
    doctor_id = await access.authorize_self_patient(
        session, actor, action="diary.entry.create", lock_patient=True
    )
    request_hash = canonical_command_hash("entry.create", body, include_defaults=True)
    prior = await replay_receipt(
        session,
        patient_id=actor.id,
        operation_id=body.operation_id,
        operation_type="entry.create",
        request_hash=request_hash,
    )
    if prior is not None:
        return DiaryCommandReceipt.model_validate(prior), False
    if body.kind == "daily" and body.daily_on != body.occurred_at.astimezone(timezone).date():
        raise DiaryError(422, "invalid_daily_date", "daily_on must match the event date")
    if await session.get(Entry, body.entry_id) is not None:
        raise DiaryError(409, "entry_exists", "Entry ID was already used")
    if body.kind == "daily":
        existing = await session.scalar(
            select(Entry.id).where(
                Entry.patient_id == actor.id,
                Entry.daily_on == body.daily_on,
            )
        )
        if existing is not None:
            raise DiaryError(409, "daily_exists", "Daily entry already exists")

    entry = Entry(
        id=body.entry_id,
        patient_id=actor.id,
        kind=body.kind,
        occurred_at=body.occurred_at,
        daily_on=body.daily_on,
        current_revision=1,
    )
    text_source = uuid4() if body.text else None
    medicine_source = uuid4() if body.other_medicine_text else None
    revision = Revision(
        entry_id=entry.id,
        revision=1,
        mood_level=body.mood_level,
        mood_words=body.mood_words,
        text=body.text,
        text_source_id=text_source,
        other_medicine_text=body.other_medicine_text,
        other_medicine_source_id=medicine_source,
    )
    session.add_all([entry, revision])
    await session.flush()
    if body.kind == "daily" and body.daily_on is not None:
        await snapshot_daily_intake(
            session,
            patient_id=actor.id,
            scheduled_on=body.daily_on,
            revision_id=revision.id,
        )
    if text_source is not None and body.text is not None:
        await _submit(session, actor.id, text_source, body.text)
    if medicine_source is not None and body.other_medicine_text is not None:
        await _submit(session, actor.id, medicine_source, body.other_medicine_text)
    receipt = DiaryCommandReceipt(
        operation_id=body.operation_id,
        entry_id=entry.id,
        revision=1,
        hidden=False,
        received_at=entry.received_at,
    )
    save_receipt(
        session,
        patient_id=actor.id,
        operation_id=body.operation_id,
        operation_type="entry.create",
        request_hash=request_hash,
        receipt=receipt,
    )
    await realtime.publish(
        session,
        [actor.id, doctor_id],
        "diary.changed",
        {"patient_id": str(actor.id), "entry_id": str(entry.id)},
    )
    await session.flush()
    return receipt, True


async def create_voice_entry(
    session: AsyncSession,
    actor: UserOut,
    body: DiaryVoiceEntryCreateIn,
    audio: DiaryAudioCreateIn,
    *,
    timezone: ZoneInfo,
) -> tuple[DiaryCommandReceipt, bool]:
    """Сохранить voice metadata в общей модели; загрузку и ASR делает voice-задача."""
    doctor_id = await access.authorize_self_patient(
        session, actor, action="diary.entry.voice.create", lock_patient=True
    )
    await lock_operation_key(session, actor.id, body.operation_id)
    request_hash = canonical_command_hash(
        "entry.voice.create", body, audio=audio, include_defaults=True
    )
    prior = await replay_receipt(
        session,
        patient_id=actor.id,
        operation_id=body.operation_id,
        operation_type="entry.voice.create",
        request_hash=request_hash,
    )
    if prior is not None:
        return DiaryCommandReceipt.model_validate(prior), False
    if body.kind == "daily" and body.daily_on != body.occurred_at.astimezone(timezone).date():
        raise DiaryError(422, "invalid_daily_date", "daily_on must match the event date")
    if await session.get(Entry, body.entry_id) is not None:
        raise DiaryError(409, "entry_exists", "Entry ID was already used")
    if body.kind == "daily":
        existing = await session.scalar(
            select(Entry.id).where(Entry.patient_id == actor.id, Entry.daily_on == body.daily_on)
        )
        if existing is not None:
            raise DiaryError(409, "daily_exists", "Daily entry already exists")
    entry = Entry(
        id=body.entry_id,
        patient_id=actor.id,
        kind=body.kind,
        occurred_at=body.occurred_at,
        daily_on=body.daily_on,
        current_revision=1,
    )
    audio_row = Audio(
        entry_id=entry.id,
        storage_key=audio.storage_key,
        sha256=audio.sha256,
        byte_size=audio.byte_size,
        mime_type=audio.mime_type,
        duration_ms=audio.duration_ms,
    )
    medicine_source = uuid4() if body.other_medicine_text else None
    session.add(entry)
    await session.flush()
    session.add(audio_row)
    await session.flush()
    await queue.enqueue(
        session,
        "diary.transcribe",
        str(audio_row.id).encode(),
        dedupe_key=f"diary.transcribe:{audio_row.id}",
    )
    revision = Revision(
        entry_id=entry.id,
        revision=1,
        mood_level=body.mood_level,
        mood_words=body.mood_words,
        other_medicine_text=body.other_medicine_text,
        other_medicine_source_id=medicine_source,
        audio_id=audio_row.id,
    )
    session.add(revision)
    await session.flush()
    if body.kind == "daily" and body.daily_on is not None:
        await snapshot_daily_intake(
            session,
            patient_id=actor.id,
            scheduled_on=body.daily_on,
            revision_id=revision.id,
        )
    if medicine_source is not None and body.other_medicine_text is not None:
        await _submit(session, actor.id, medicine_source, body.other_medicine_text)
    receipt = DiaryCommandReceipt(
        operation_id=body.operation_id,
        entry_id=entry.id,
        revision=1,
        hidden=False,
        received_at=entry.received_at,
    )
    save_receipt(
        session,
        patient_id=actor.id,
        operation_id=body.operation_id,
        operation_type="entry.voice.create",
        request_hash=request_hash,
        receipt=receipt,
    )
    await realtime.publish(
        session,
        [actor.id, doctor_id],
        "diary.changed",
        {"patient_id": str(actor.id), "entry_id": str(entry.id)},
    )
    await session.flush()
    return receipt, True


async def patch_entry(
    session: AsyncSession,
    actor: UserOut,
    entry_id: UUID,
    body: DiaryEntryPatchIn,
) -> tuple[DiaryCommandReceipt, bool]:
    """Добавить неизменяемую версию, переиспользуя неизменившиеся source ID."""
    entry, doctor_id = await access.authorize_patient_entry(
        session,
        actor,
        entry_id=entry_id,
        action="diary.entry.edit",
        lock_patient=True,
    )
    request_hash = canonical_command_hash("entry.edit", body)
    prior = await replay_receipt(
        session,
        patient_id=actor.id,
        operation_id=body.operation_id,
        operation_type="entry.edit",
        request_hash=request_hash,
    )
    if prior is not None:
        return DiaryCommandReceipt.model_validate(prior), False
    if entry is None or entry.patient_id != actor.id or entry.hidden_at is not None:
        raise DiaryError(404, "not_found", "Diary entry not found")
    previous = await _current_revision(session, entry)
    if body.expected_revision != entry.current_revision:
        raise DiaryError(
            409, "revision_conflict", "Entry has changed", current_version=entry.current_revision
        )
    values = body.model_dump(exclude_unset=True)
    mood_level = values.get("mood_level", previous.mood_level)
    mood_words = values.get("mood_words", previous.mood_words)
    if mood_words is None:
        mood_words = []
    if mood_level is None and mood_words:
        raise DiaryError(422, "invalid_mood_words", "Mood words require a mood level")
    if entry.kind in {"daily", "mood"} and mood_level is None:
        raise DiaryError(422, "mood_required", "Daily and mood entries require mood_level")
    text_value = values.get("text", previous.text)
    medicine_value = values.get("other_medicine_text", previous.other_medicine_text)
    if entry.kind != "daily" and medicine_value is not None:
        raise DiaryError(
            422,
            "other_medicine_only_daily",
            "other_medicine_text is allowed only for daily entries",
        )
    intake_mark_ids = (
        list(
            await session.scalars(
                select(RevisionIntakeMark.intake_mark_id).where(
                    RevisionIntakeMark.revision_id == previous.id
                )
            )
        )
        if entry.kind == "daily"
        else []
    )
    text_source = previous.text_source_id
    medicine_source = previous.other_medicine_source_id
    if "text" in values and text_value != previous.text:
        text_source = uuid4() if text_value else None
    if "other_medicine_text" in values and medicine_value != previous.other_medicine_text:
        medicine_source = uuid4() if medicine_value else None

    next_revision = entry.current_revision + 1
    revision = Revision(
        entry_id=entry.id,
        revision=next_revision,
        mood_level=mood_level,
        mood_words=mood_words,
        text=text_value,
        text_source_id=text_source,
        other_medicine_text=medicine_value,
        other_medicine_source_id=medicine_source,
        audio_id=previous.audio_id,
    )
    session.add(revision)
    entry.current_revision = next_revision
    await session.flush()
    if intake_mark_ids:
        session.add_all(
            [
                RevisionIntakeMark(revision_id=revision.id, intake_mark_id=mark_id)
                for mark_id in intake_mark_ids
            ]
        )
    if "text" in values and text_value is not None and text_value != previous.text:
        assert text_source is not None
        await _submit(session, actor.id, text_source, text_value)
    if (
        "other_medicine_text" in values
        and medicine_value is not None
        and medicine_value != previous.other_medicine_text
    ):
        assert medicine_source is not None
        await _submit(session, actor.id, medicine_source, medicine_value)
    receipt = DiaryCommandReceipt(
        operation_id=body.operation_id,
        entry_id=entry.id,
        revision=next_revision,
        hidden=False,
        received_at=datetime.now(UTC),
    )
    save_receipt(
        session,
        patient_id=actor.id,
        operation_id=body.operation_id,
        operation_type="entry.edit",
        request_hash=request_hash,
        receipt=receipt,
    )
    await realtime.publish(
        session,
        [actor.id, doctor_id],
        "diary.changed",
        {"patient_id": str(actor.id), "entry_id": str(entry.id)},
    )
    await session.flush()
    return receipt, True


async def _current_revision(session: AsyncSession, entry: Entry) -> Revision:
    row = await session.scalar(
        select(Revision).where(
            Revision.entry_id == entry.id,
            Revision.revision == entry.current_revision,
        )
    )
    if row is None:
        raise RuntimeError("diary entry has no current revision")
    return row


async def _submit(session: AsyncSession, patient_id: UUID, source_id: UUID, text: str) -> None:
    try:
        await freetext.submit_text(
            session,
            patient_id=patient_id,
            source_type="diary",
            source_id=source_id,
            text=text,
            transcript=False,
        )
    except ValueError as error:
        raise DiaryError(422, "invalid_text", str(error)) from error
