"""Публичные HTTP-типы и интерфейсы дневника."""

from datetime import date
from typing import Any, Literal
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    field_validator,
    model_validator,
)

from clin_compass.modules.freetext.schemas import AnalysisStatus, FindingOut
from clin_compass.modules.proposals.schemas import ProposalOut

EntryKind = Literal["daily", "mood", "text", "voice"]
MoodWord = Literal[
    "спокойствие",
    "радость",
    "интерес",
    "усталость",
    "тревога",
    "напряжение",
    "раздражение",
    "грусть",
    "одиночество",
    "плохой сон",
]
DiaryFilter = Literal["all", "mood", "records"]
SourceField = Literal["text", "other_medicine", "transcript"]


class DiaryEntryCreateIn(BaseModel):
    """JSON-команда создания daily, mood или text записи."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    operation_id: UUID
    entry_id: UUID
    kind: Literal["daily", "mood", "text"]
    occurred_at: AwareDatetime
    daily_on: date | None = None
    mood_level: StrictInt | None = Field(default=None, ge=1, le=5)
    mood_words: list[MoodWord] = Field(default_factory=list)
    text: str | None = Field(default=None, max_length=20_000)
    other_medicine_text: str | None = Field(default=None, max_length=20_000)

    @field_validator("text", "other_medicine_text")
    @classmethod
    def _strip_text(cls, value: str | None) -> str | None:
        cleaned = value.strip() if value is not None else None
        return cleaned or None

    @field_validator("mood_words")
    @classmethod
    def _unique_words(cls, value: list[MoodWord]) -> list[MoodWord]:
        return list(dict.fromkeys(value))

    @model_validator(mode="after")
    def _check_kind_fields(self) -> "DiaryEntryCreateIn":
        if (self.kind == "daily") != (self.daily_on is not None):
            raise ValueError("daily_on is required only for daily entries")
        if self.kind in {"daily", "mood"} and self.mood_level is None:
            raise ValueError("daily and mood entries require mood_level")
        if self.kind == "text" and self.text is None:
            raise ValueError("text entries require non-empty text")
        if self.other_medicine_text is not None and self.kind != "daily":
            raise ValueError("other_medicine_text is allowed only for daily entries")
        if self.mood_level is None and self.mood_words:
            raise ValueError("mood_words require mood_level")
        return self


class DiaryVoiceEntryCreateIn(BaseModel):
    """Метаинформация команды создания voice-записи."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    operation_id: UUID
    entry_id: UUID
    kind: Literal["daily", "voice"]
    occurred_at: AwareDatetime
    daily_on: date | None = None
    mood_level: StrictInt | None = Field(default=None, ge=1, le=5)
    mood_words: list[MoodWord] = Field(default_factory=list)
    other_medicine_text: str | None = Field(default=None, max_length=20_000)

    @field_validator("other_medicine_text")
    @classmethod
    def _strip_text(cls, value: str | None) -> str | None:
        cleaned = value.strip() if value is not None else None
        return cleaned or None

    @field_validator("mood_words")
    @classmethod
    def _unique_words(cls, value: list[MoodWord]) -> list[MoodWord]:
        return list(dict.fromkeys(value))

    @model_validator(mode="after")
    def _check_kind_fields(self) -> "DiaryVoiceEntryCreateIn":
        if (self.kind == "daily") != (self.daily_on is not None):
            raise ValueError("daily_on is required only for daily entries")
        if self.kind == "daily" and self.mood_level is None:
            raise ValueError("daily entries require mood_level")
        if self.mood_level is None and self.mood_words:
            raise ValueError("mood_words require mood_level")
        return self


class DiaryVoiceUploadMetadataIn(DiaryVoiceEntryCreateIn):
    """Метаинформация multipart-команды вместе с заявленной длительностью аудио."""

    duration_ms: StrictInt = Field(ge=1, le=600_000)


class DiaryAudioCreateIn(BaseModel):
    """Уже загруженный объект S3, который фиксируется вместе с записью."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    storage_key: str = Field(min_length=1, max_length=500)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    byte_size: StrictInt = Field(ge=1, le=25 * 1024 * 1024)
    mime_type: Literal["audio/webm", "audio/mp4"]
    duration_ms: StrictInt = Field(ge=1, le=600_000)


class DiaryEntryPatchIn(BaseModel):
    """Команда новой версии. Переданные `null` очищают поле."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    operation_id: UUID
    expected_revision: StrictInt = Field(ge=1)
    mood_level: StrictInt | None = Field(default=None, ge=1, le=5)
    mood_words: list[MoodWord] | None = None
    text: str | None = Field(default=None, max_length=20_000)
    other_medicine_text: str | None = Field(default=None, max_length=20_000)

    @field_validator("text", "other_medicine_text")
    @classmethod
    def _strip_text(cls, value: str | None) -> str | None:
        cleaned = value.strip() if value is not None else None
        return cleaned or None

    @field_validator("mood_words")
    @classmethod
    def _unique_words(cls, value: list[MoodWord] | None) -> list[MoodWord] | None:
        return list(dict.fromkeys(value)) if value is not None else None

    @model_validator(mode="after")
    def _requires_change(self) -> "DiaryEntryPatchIn":
        if not self.model_fields_set.intersection(
            {"mood_level", "mood_words", "text", "other_medicine_text"}
        ):
            raise ValueError("at least one entry field must be provided")
        return self


class DiaryHideIn(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operation_id: UUID
    expected_revision: StrictInt = Field(ge=1)


class DiaryCommandReceipt(BaseModel):
    """Квитанция без содержимого записи, безопасная для повторов после скрытия."""

    model_config = ConfigDict(frozen=True)

    operation_id: UUID
    entry_id: UUID
    revision: int
    hidden: bool
    received_at: AwareDatetime


class DiaryAudioOut(BaseModel):
    """Метаданные собственного аудио без закрытого ключа хранения."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    status: Literal["pending", "processing", "done", "failed"]
    duration_ms: int
    transcript: str | None
    transcript_source_id: UUID | None


class DiaryTranscriptionRetryIn(BaseModel):
    """Повторная команда пациента для терминально завершившейся расшифровки."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    operation_id: UUID


class DiaryTranscriptionRetryOut(BaseModel):
    """Квитанция о постановке аудио на повторную расшифровку."""

    model_config = ConfigDict(frozen=True)

    operation_id: UUID
    entry_id: UUID
    audio_id: UUID
    status: Literal["pending"]


class DiarySourceStatusOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    field: SourceField
    source_id: UUID
    status: AnalysisStatus | None


class DiaryEntryOut(BaseModel):
    """Текущая видимая версия записи."""

    model_config = ConfigDict(frozen=True)

    entry_id: UUID
    kind: EntryKind
    occurred_at: AwareDatetime
    daily_on: date | None
    revision: int
    received_at: AwareDatetime
    mood_level: int | None
    mood_words: list[str]
    text: str | None
    other_medicine_text: str | None
    audio: DiaryAudioOut | None
    source_statuses: list[DiarySourceStatusOut]


class DiaryDoctorSourceOut(BaseModel):
    """Находки врача относятся к точной неизменяемой версии текста."""

    model_config = ConfigDict(frozen=True)

    field: SourceField
    source_id: UUID
    revision: int
    status: AnalysisStatus | None
    proposals: list[ProposalOut]
    findings: list[FindingOut]


class DiaryDoctorEntryOut(DiaryEntryOut):
    sources: list[DiaryDoctorSourceOut]


class DiaryEntryPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: list[DiaryEntryOut]
    next_cursor: str | None


class DiaryDoctorEntryPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: list[DiaryDoctorEntryOut]
    next_cursor: str | None


class DiaryMoodPoint(BaseModel):
    model_config = ConfigDict(frozen=True)

    entry_id: UUID
    occurred_at: AwareDatetime
    daily_on: date | None
    mood_level: int
    mood_words: list[str]


class DiaryMoodPointsOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: list[DiaryMoodPoint]


class DiaryContextOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    server_now: AwareDatetime
    today: date
    timezone: str
    observation_from: date
    daily_status: Literal["saved", "missing"]
    month_entry_count: int


class IntakeMarkCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operation_id: UUID
    prescription_id: UUID
    scheduled_on: date
    dose_ordinal: StrictInt = Field(ge=1, le=12)
    status: Literal["taken", "unmarked"]
    occurred_at: AwareDatetime


class IntakeMarkOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    prescription_id: UUID
    scheduled_on: date
    dose_ordinal: int
    status: Literal["taken", "unmarked"]
    occurred_at: AwareDatetime
    source: Literal["patient_report"]
    version: int
    scheme_snapshot: dict[str, Any]
    received_at: AwareDatetime


class IntakeMarksOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: list[IntakeMarkOut]
