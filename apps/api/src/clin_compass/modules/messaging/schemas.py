"""Публичные схемы HTTP API модуля messaging."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator

AnalysisStatus = Literal["processing", "done", "failed"]
TranscriptionStatus = Literal["queued", "processing", "done", "failed"]
TranscriptionErrorCode = Literal["asr_rejected", "audio_missing", "transcription_failed"]
AnalysisSkipReason = Literal["empty_transcript", "transcript_too_long", "doctor_message"]

# Номера сообщений и курсоры хранятся в Postgres integer; больший курсор asyncpg не передаст.
SEQ_MAX = 2**31 - 1
# `active=true` команды «печатает» принимается не чаще раза в этот интервал на подключение и пару.
TYPING_MIN_INTERVAL_SECONDS = 2


class MessageCreateIn(BaseModel):
    """Текстовое сообщение; отправителя и получателя сервер определяет сам."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    expected_doctor_id: UUID
    client_message_id: UUID
    text: str = Field(min_length=1, max_length=20_000)

    @field_validator("text")
    @classmethod
    def _require_nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text must contain a non-whitespace character")
        # Postgres text не хранит NUL: без этой проверки INSERT упал бы с ответом 503.
        if "\x00" in value:
            raise ValueError("text must not contain NUL characters")
        return value


class MessageOut(BaseModel):
    """Сообщение без внутренних ключей хранилища и данных SDK."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    seq: StrictInt
    sender_id: UUID
    client_message_id: UUID
    kind: Literal["text", "voice"]
    created_at: datetime
    text: str | None
    audio_mime: str | None = None
    audio_size: StrictInt | None = None
    transcription_status: TranscriptionStatus | None
    transcription_text: str | None
    duration_ms: StrictInt | None
    transcription_error_code: TranscriptionErrorCode | None
    analysis_status: AnalysisStatus | None
    analysis_skip_reason: AnalysisSkipReason | None


class MessageHistoryOut(BaseModel):
    """Страница истории и снимок курсоров диалога."""

    model_config = ConfigDict(frozen=True)

    items: list[MessageOut]
    doctor_id: UUID
    last_seq: StrictInt
    read_through_seq: StrictInt
    peer_read_through_seq: StrictInt
    unread_count: StrictInt
    next_before_seq: StrictInt | None
    next_after_seq: StrictInt | None


class ReadCreateIn(BaseModel):
    """Явное подтверждение прочтения до номера сообщения."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    expected_doctor_id: UUID
    through_seq: StrictInt


class ReadOut(BaseModel):
    """Текущий курсор прочтения и число входящих сообщений после него."""

    model_config = ConfigDict(frozen=True)

    read_through_seq: StrictInt
    unread_count: StrictInt


class UnreadOut(BaseModel):
    """Сводка непрочитанного без текста переписки."""

    model_config = ConfigDict(frozen=True)

    patient_id: UUID
    doctor_id: UUID
    unread_count: StrictInt
    last_message_id: UUID
    last_message_at: datetime


class MessagingStatusOut(BaseModel):
    """Снимок текущей пары и состояния собеседника."""

    model_config = ConfigDict(frozen=True)

    patient_id: UUID
    doctor_id: UUID
    peer_full_name: str
    online: bool
    last_seen_at: datetime | None
    typing_until: datetime | None


class TypingCommand(BaseModel):
    """Входящая WS-команда; отправителя всегда определяет серверная сессия."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    patient_id: UUID
    expected_doctor_id: UUID
    active: StrictBool
