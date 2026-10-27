"""Команды и допуск загрузки голосовых записей дневника."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from clin_compass.core import queue, realtime
from clin_compass.modules.diary import access
from clin_compass.modules.diary.errors import DiaryError
from clin_compass.modules.diary.models import Audio
from clin_compass.modules.diary.operations import (
    canonical_command_hash,
    lock_operation_key,
    replay_receipt,
    save_receipt,
)
from clin_compass.modules.diary.schemas import (
    DiaryAudioCreateIn,
    DiaryCommandReceipt,
    DiaryTranscriptionRetryIn,
    DiaryTranscriptionRetryOut,
    DiaryVoiceEntryCreateIn,
)
from clin_compass.modules.identity.schemas import UserOut


async def check_voice_replay(
    session: AsyncSession,
    actor: UserOut,
    body: DiaryVoiceEntryCreateIn,
    audio: DiaryAudioCreateIn,
) -> DiaryCommandReceipt | None:
    """Return a matching receipt before upload, or reject a reused operation key."""
    await access.authorize_self_patient(
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
    return DiaryCommandReceipt.model_validate(prior) if prior is not None else None


async def validate_voice_actor(session: AsyncSession, actor: UserOut) -> None:
    """Reject non-patients before parsing or writing an uploaded audio object."""
    await access.authorize_self_patient(
        session, actor, action="diary.entry.voice.create", lock_patient=True
    )


async def retry_transcription(
    session: AsyncSession,
    actor: UserOut,
    entry_id: UUID,
    body: DiaryTranscriptionRetryIn,
) -> tuple[DiaryTranscriptionRetryOut, bool]:
    """Requeue only terminal ASR failures, with a durable idempotency receipt."""
    entry, doctor_id = await access.authorize_patient_entry(
        session,
        actor,
        entry_id=entry_id,
        action="diary.entry.transcription.retry",
        lock_patient=True,
    )
    if entry is None or entry.patient_id != actor.id or entry.hidden_at is not None:
        raise DiaryError(404, "not_found", "Diary entry not found")
    audio = await session.scalar(select(Audio).where(Audio.entry_id == entry.id).with_for_update())
    if audio is None:
        raise DiaryError(404, "not_found", "Diary audio not found")

    await lock_operation_key(session, actor.id, body.operation_id)
    request_hash = canonical_command_hash(f"audio.retry:{entry_id}", body)
    prior = await replay_receipt(
        session,
        patient_id=actor.id,
        operation_id=body.operation_id,
        operation_type="audio.retry",
        request_hash=request_hash,
    )
    if prior is not None:
        return DiaryTranscriptionRetryOut.model_validate(prior), False
    if audio.status != "failed":
        raise DiaryError(409, "retry_not_allowed", "Only failed transcription can be retried")

    audio.status = "pending"
    audio.failures = 0
    audio.transcript = None
    audio.transcript_source_id = None
    audio.asr_run_id = None
    receipt = DiaryTranscriptionRetryOut(
        operation_id=body.operation_id,
        entry_id=entry.id,
        audio_id=audio.id,
        status="pending",
    )
    save_receipt(
        session,
        patient_id=actor.id,
        operation_id=body.operation_id,
        operation_type="audio.retry",
        request_hash=request_hash,
        receipt=receipt,
    )
    await queue.enqueue(
        session,
        "diary.transcribe",
        str(audio.id).encode(),
        dedupe_key=f"diary.transcribe:{audio.id}:{body.operation_id}",
    )
    await realtime.publish(
        session,
        [actor.id, doctor_id],
        "diary.changed",
        {"patient_id": str(actor.id), "entry_id": str(entry.id), "audio_status": "pending"},
    )
    await session.flush()
    return receipt, True
