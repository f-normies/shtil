"""Публичный сервис дневника: доступ, команды, чтения и журналирование."""

from datetime import date
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from clin_compass.modules.diary import (
    audio_access,
    doctor_queries,
    entry_commands,
    hide_commands,
    intake_commands,
    queries,
    voice_commands,
)
from clin_compass.modules.diary.audio_access import AudioReference
from clin_compass.modules.diary.schemas import (
    DiaryAudioCreateIn,
    DiaryCommandReceipt,
    DiaryContextOut,
    DiaryDoctorEntryPage,
    DiaryEntryCreateIn,
    DiaryEntryOut,
    DiaryEntryPage,
    DiaryEntryPatchIn,
    DiaryFilter,
    DiaryHideIn,
    DiaryMoodPointsOut,
    DiaryTranscriptionRetryIn,
    DiaryTranscriptionRetryOut,
    DiaryVoiceEntryCreateIn,
    IntakeMarkCreateIn,
    IntakeMarkOut,
    IntakeMarksOut,
)
from clin_compass.modules.identity.schemas import UserOut

__all__ = [
    "AudioReference",
    "check_voice_replay",
    "context",
    "create_entry",
    "create_intake_mark",
    "create_voice_entry",
    "doctor_audio",
    "doctor_moods",
    "get_patient_entry",
    "hide_entry",
    "list_doctor_entries",
    "list_intake_marks",
    "list_patient_entries",
    "list_patient_intake_marks",
    "patch_entry",
    "patient_audio",
    "retry_transcription",
    "validate_voice_actor",
]


async def create_entry(
    session: AsyncSession,
    actor: UserOut,
    body: DiaryEntryCreateIn,
    *,
    timezone: ZoneInfo,
) -> tuple[DiaryCommandReceipt, bool]:
    """Создать запись и вернуть квитанцию с признаком первого принятия."""
    return await entry_commands.create_entry(session, actor, body, timezone=timezone)


async def create_voice_entry(
    session: AsyncSession,
    actor: UserOut,
    body: DiaryVoiceEntryCreateIn,
    audio: DiaryAudioCreateIn,
    *,
    timezone: ZoneInfo,
) -> tuple[DiaryCommandReceipt, bool]:
    """Зафиксировать voice-запись после помещения закрытого объекта в S3."""
    return await entry_commands.create_voice_entry(session, actor, body, audio, timezone=timezone)


async def check_voice_replay(
    session: AsyncSession,
    actor: UserOut,
    body: DiaryVoiceEntryCreateIn,
    audio: DiaryAudioCreateIn,
) -> DiaryCommandReceipt | None:
    """Check an operation receipt before writing a second private audio object."""
    return await voice_commands.check_voice_replay(session, actor, body, audio)


async def validate_voice_actor(session: AsyncSession, actor: UserOut) -> None:
    """Check patient ownership before parsing a potentially large voice upload."""
    await voice_commands.validate_voice_actor(session, actor)


async def retry_transcription(
    session: AsyncSession,
    actor: UserOut,
    entry_id: UUID,
    body: DiaryTranscriptionRetryIn,
) -> tuple[DiaryTranscriptionRetryOut, bool]:
    """Повторно поставить терминально завершившуюся расшифровку в очередь."""
    return await voice_commands.retry_transcription(session, actor, entry_id, body)


async def patient_audio(session: AsyncSession, actor: UserOut, entry_id: UUID) -> AudioReference:
    """Авторизовать владельца и вернуть закрытые данные потока аудио."""
    return await audio_access.patient_audio(session, actor, entry_id)


async def doctor_audio(
    session: AsyncSession, actor: UserOut, patient_id: UUID, entry_id: UUID
) -> AudioReference:
    """Проверить текущего ведущего врача и вернуть закрытые данные потока."""
    return await audio_access.doctor_audio(session, actor, patient_id, entry_id)


async def patch_entry(
    session: AsyncSession,
    actor: UserOut,
    entry_id: UUID,
    body: DiaryEntryPatchIn,
) -> tuple[DiaryCommandReceipt, bool]:
    """Добавить новую версию записи по ожидаемой версии."""
    return await entry_commands.patch_entry(session, actor, entry_id, body)


async def hide_entry(
    session: AsyncSession,
    actor: UserOut,
    entry_id: UUID,
    body: DiaryHideIn,
) -> tuple[DiaryCommandReceipt, bool]:
    """Скрыть запись, оставив версии, анализы и отметки неизменными."""
    return await hide_commands.hide_entry(session, actor, entry_id, body)


async def context(session: AsyncSession, actor: UserOut, *, timezone: ZoneInfo) -> DiaryContextOut:
    """Получить серверную дату и число видимых записей месяца."""
    return await queries.context(session, actor, timezone=timezone)


async def get_patient_entry(session: AsyncSession, actor: UserOut, entry_id: UUID) -> DiaryEntryOut:
    """Получить текущую запись пациента без врачебных находок."""
    return await queries.get_patient_entry(session, actor, entry_id)


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
    """Получить собственную ленту пациента."""
    return await queries.list_patient_entries(
        session,
        actor,
        timezone=timezone,
        filter_=filter_,
        start=start,
        end=end,
        cursor=cursor,
        limit=limit,
    )


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
    """Проверить ведущего врача и вернуть его страницу дневника с находками."""
    return await doctor_queries.list_doctor_entries(
        session,
        doctor_id=doctor_id,
        patient_id=patient_id,
        start=start,
        end=end,
        timezone=timezone,
        filter_=filter_,
        cursor=cursor,
        limit=limit,
    )


async def doctor_moods(
    session: AsyncSession,
    *,
    doctor_id: UUID,
    patient_id: UUID,
    start: date,
    end: date,
    timezone: ZoneInfo,
) -> DiaryMoodPointsOut:
    """Вернуть все текущие точки настроения за период."""
    return await doctor_queries.doctor_moods(
        session,
        doctor_id=doctor_id,
        patient_id=patient_id,
        start=start,
        end=end,
        timezone=timezone,
    )


async def create_intake_mark(
    session: AsyncSession,
    actor: UserOut,
    body: IntakeMarkCreateIn,
) -> tuple[IntakeMarkOut, bool]:
    """Сохранить версию taken/unmarked после проверки назначения на дату."""
    return await intake_commands.create_intake_mark(session, actor, body)


async def list_patient_intake_marks(
    session: AsyncSession, actor: UserOut, *, scheduled_on: date
) -> IntakeMarksOut:
    """Прочитать текущие отметки пациента о приёме за дату."""
    return await intake_commands.list_patient_intake_marks(
        session, actor, scheduled_on=scheduled_on
    )


async def list_intake_marks(
    session: AsyncSession,
    *,
    doctor_id: UUID,
    patient_id: UUID,
    start: date,
    end: date,
) -> IntakeMarksOut:
    """Публичный контракт #34: отметки слотов и снимки действовавших схем."""
    return await intake_commands.list_intake_marks(
        session,
        doctor_id=doctor_id,
        patient_id=patient_id,
        start=start,
        end=end,
    )
