"""Формирование публичных представлений сообщений без внутренних полей модели."""

from collections.abc import Mapping
from uuid import UUID

from clin_compass.modules.freetext.service import MAX_TEXT_LENGTH
from clin_compass.modules.messaging.models import Conversation, Message
from clin_compass.modules.messaging.schemas import AnalysisStatus, MessageOut


def message_out(
    message: Message, conversation: Conversation, statuses: Mapping[UUID, AnalysisStatus]
) -> MessageOut:
    """Преобразовать строку сообщения в безопасный ответ REST без ключей хранилища."""
    is_doctor_message = message.sender_id == conversation.doctor_id
    return MessageOut(
        id=message.id,
        seq=message.seq,
        sender_id=message.sender_id,
        client_message_id=message.client_message_id,
        kind=message.kind,
        created_at=message.created_at,
        text=message.text,
        audio_mime=message.audio_mime,
        audio_size=message.audio_size,
        transcription_status=message.transcription_status,
        transcription_text=message.transcription_text,
        duration_ms=message.duration_ms,
        transcription_error_code=message.transcription_error_code,
        analysis_status=statuses.get(message.id) if not is_doctor_message else None,
        analysis_skip_reason=_analysis_skip_reason(message, is_doctor_message),
    )


def _analysis_skip_reason(message: Message, is_doctor_message: bool) -> str | None:
    if is_doctor_message:
        return "doctor_message"
    if message.kind != "voice" or message.transcription_status != "done":
        return None
    transcript = message.transcription_text or ""
    if not transcript.strip():
        return "empty_transcript"
    if len(transcript) > MAX_TEXT_LENGTH:
        return "transcript_too_long"
    return None
