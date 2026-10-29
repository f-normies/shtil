"""Публичные схемы каталога и результатов заполнения шкал."""

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from clin_compass_knowledge.schemas.scale import Scale
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictBool, StrictInt


class SourceOut(BaseModel):
    """Источник шкалы с названием и статусом дополнительной публикации."""

    model_config = ConfigDict(frozen=True)

    id: str
    title: str
    url: str
    supplementary: bool


class ScaleDefinitionOut(BaseModel):
    """Текущее определение шкалы и проверенные источники из манифеста."""

    model_config = ConfigDict(frozen=True)

    scale: Scale
    sources: list[SourceOut]


class ScaleCatalogOut(BaseModel):
    """Доступные читателю определения шкал."""

    model_config = ConfigDict(frozen=True)

    items: list[ScaleDefinitionOut]


class AssessmentCreateIn(BaseModel):
    """Заполненная шкала; `submission_id` — ключ идемпотентности отправки."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    patient_id: UUID
    submission_id: UUID
    scale_id: str = Field(min_length=1)
    scale_version: StrictInt = Field(ge=1)
    completed_at: AwareDatetime
    answers: dict[str, StrictInt]


class ClinicianAssessmentIn(BaseModel):
    """Результат шкалы врача (#94). Пациент — из пути, время ставит сервер."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    submission_id: UUID
    scale_id: str = Field(min_length=1)
    scale_version: StrictInt = Field(ge=1)
    answers: dict[str, StrictInt]


class ScoreBandSourceOut(BaseModel):
    """Источник порога с местом в документе и метаданными манифеста."""

    model_config = ConfigDict(frozen=True)

    id: str
    locator: str
    title: str
    url: str
    supplementary: bool


class ScoreBandOut(BaseModel):
    """Код и подпись диапазона тяжести с источником порога."""

    model_config = ConfigDict(frozen=True)

    code: str
    label: str
    source: ScoreBandSourceOut


class AssessmentScoreOut(BaseModel):
    """Рассчитанная сумма и определённый для неё диапазон."""

    model_config = ConfigDict(frozen=True)

    value: StrictInt
    band: ScoreBandOut


class AssessmentOut(BaseModel):
    """Сохранённый результат со снимком ответов, подсчёта и источников."""

    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    submission_id: UUID
    patient_id: UUID
    scale_id: str
    scale_version: int
    scale_title: str
    completed_at: datetime
    received_at: datetime
    answers: dict[str, StrictInt]
    scores: dict[str, AssessmentScoreOut]
    sources: list[SourceOut] = Field(validation_alias="sources_snapshot")
    # Врач, внёсший результат; None — самоотчёт пациента (#94).
    recorded_by: UUID | None = None


class AssessmentCreatedOut(AssessmentOut):
    """Ответ на отправку пациента: `alert_raised` — по ней есть сигнал `kind: alert` (#57).

    По признаку PWA пишет на экране помощи «Мы сообщили вашему врачу».
    """

    alert_raised: bool

    @classmethod
    def of(cls, result: AssessmentOut, *, alert_raised: bool) -> "AssessmentCreatedOut":
        """Результат с признаком сигнала. Источники читаются по имени `sources_snapshot`."""
        data = result.model_dump(exclude={"sources"})
        return cls.model_validate(
            {**data, "sources_snapshot": result.sources, "alert_raised": alert_raised}
        )


class HistoryQuery(BaseModel):
    """Фильтр по шкале и курсор страницы истории. Границы `limit` проверяет HTTP-слой."""

    model_config = ConfigDict(frozen=True)

    scale_id: str | None = None
    limit: int = 20
    before: UUID | None = None


class AssessmentHistoryOut(BaseModel):
    """Страница результатов с курсором для следующей страницы истории."""

    model_config = ConfigDict(frozen=True)

    items: list[AssessmentOut]
    next_cursor: UUID | None


class ScaleDueOut(BaseModel):
    """День опроса по шкале для пациента; опрос положен, если `due_on` не позже `today`."""

    model_config = ConfigDict(frozen=True)

    scale_id: str
    scale_title: str
    due_on: date


class ScheduleOut(BaseModel):
    """Расписание опросов пациента; `today` — сегодня в поясе расписания."""

    model_config = ConfigDict(frozen=True)

    today: date
    items: list[ScaleDueOut]


class PlannedDateOut(BaseModel):
    """Плановая дата шкалы; `missed` — пропуск на графике врача."""

    model_config = ConfigDict(from_attributes=True, frozen=True)

    planned_on: date
    status: Literal["done", "missed", "due", "upcoming"]
    assessment_id: UUID | None


class ScalePlanOut(BaseModel):
    """Плановые даты шкалы от регистрации до ближайшей будущей."""

    model_config = ConfigDict(frozen=True)

    scale_id: str
    period_days: int
    grace_days: int
    planned: list[PlannedDateOut]


class PatientScheduleOut(BaseModel):
    """Плановые даты шкал пациента для ведущего врача."""

    model_config = ConfigDict(frozen=True)

    today: date
    items: list[ScalePlanOut]


class ScaleSettingIn(BaseModel):
    """Настройка опроса шкалы у пациента: включена ли и период из допустимых (спека #180)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: StrictBool
    period_days: StrictInt


class ScaleSettingOut(BaseModel):
    """Действующая настройка шкалы; `updated_*` пусты, пока врач её не менял."""

    model_config = ConfigDict(frozen=True)

    scale_id: str
    scale_title: str
    enabled: bool
    period_days: int
    default_period_days: int
    updated_at: datetime | None
    updated_by: UUID | None
    updated_by_name: str | None


class ScaleSettingsOut(BaseModel):
    """Настройки шкал расписания пациента и периоды, из которых выбирает врач."""

    model_config = ConfigDict(frozen=True)

    allowed_period_days: list[int]
    items: list[ScaleSettingOut]
