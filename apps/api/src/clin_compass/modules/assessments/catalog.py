"""Каталог проверенных шкал и сверка определений с принятыми снимками.

Отдельно от `service.py`, чтобы тот не превысил 400 строк. Другие модули берут эти имена
из `service.py`: он их переэкспортирует.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

import structlog
from clin_compass_knowledge.loader import Knowledge
from clin_compass_knowledge.schemas.scale import Scale
from clin_compass_knowledge.schemas.scale_schedule import ScaleSchedule
from clin_compass_knowledge.schemas.source import Source
from sqlalchemy import select, tuple_
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.ext.asyncio import AsyncSession

from clin_compass.modules.assessments.models import AssessmentResult
from clin_compass.modules.assessments.schemas import (
    ScaleCatalogOut,
    ScaleDefinitionOut,
    SourceOut,
)

log = structlog.get_logger()

Respondent = Literal["patient", "clinician"]


class AssessmentError(Exception):
    """Ожидаемая ошибка записи результата с устойчивым HTTP-кодом."""

    def __init__(self, status: int, code: str, detail: str, **fields: object) -> None:
        super().__init__(detail)
        self.status = status
        self.code = code
        self.detail = detail
        self.fields = fields


def definition_snapshot(scale: Scale) -> dict[str, Any]:
    """Снимок определения пары (scale_id, version).

    Поля со значением по умолчанию не входят: новое необязательное поле схемы knowledge
    не меняет принятые определения.
    """
    return scale.model_dump(mode="json", exclude_defaults=True)


@dataclass(frozen=True)
class ScaleCatalog:
    """Набор проверенных определений и источников, загруженный при старте API."""

    scales: Mapping[str, Scale]
    sources: Mapping[str, Source]
    # Расписание опросов (спека #111); None — файла в knowledge/ нет.
    schedule: ScaleSchedule | None = None

    @classmethod
    def from_knowledge(cls, knowledge: Knowledge) -> "ScaleCatalog":
        """Создать каталог из результатов штатной загрузки `knowledge/`."""
        return cls(
            scales=knowledge.scales, sources=knowledge.sources, schedule=knowledge.scale_schedule
        )

    def list_available(self, respondent: Respondent = "patient") -> ScaleCatalogOut:
        """Шкалы, которые заполняет `respondent`, в стабильном порядке по id."""
        return ScaleCatalogOut(
            items=[
                self._definition(self.scales[scale_id])
                for scale_id in sorted(self.scales)
                if self.is_available(self.scales[scale_id], respondent)
            ]
        )

    def get_available(
        self, scale_id: str, respondent: Respondent | None = "patient"
    ) -> ScaleDefinitionOut | None:
        """Шкала, если она прошла условия выдачи; `respondent=None` — любой заполняющий."""
        scale = self.scales.get(scale_id)
        if scale is None or not self.is_available(scale, respondent):
            return None
        return self._definition(scale)

    @staticmethod
    def is_available(scale: Scale, respondent: Respondent | None = "patient") -> bool:
        """Шкала проверена, разрешена лицензией и заполняется `respondent` (None — любым)."""
        return (
            scale.status == "reviewed"
            and scale.license.allowed
            and (respondent is None or scale.respondent == respondent)
        )

    def _definition(self, scale: Scale) -> ScaleDefinitionOut:
        source_ids = dict.fromkeys(
            [scale.license.source.source]
            + [source.source for source in scale.validation]
            + [band.source.source for score in scale.scores for band in score.bands]
        )
        return ScaleDefinitionOut(
            scale=scale,
            sources=[
                SourceOut(
                    id=source_id,
                    title=self.sources[source_id].title,
                    url=str(self.sources[source_id].url),
                    supplementary=self.sources[source_id].supplementary,
                )
                for source_id in source_ids
            ],
        )


async def assert_consistent(session: AsyncSession, scales: Sequence[Scale]) -> None:
    """Сверить определения с базовыми снимками их пар одним запросом."""
    if not scales:
        return
    pairs = [(scale.id, scale.version) for scale in scales]
    rows = await session.execute(
        select(
            AssessmentResult.scale_id,
            AssessmentResult.scale_version,
            AssessmentResult.definition_snapshot,
        )
        .ext(distinct_on(AssessmentResult.scale_id, AssessmentResult.scale_version))
        .where(tuple_(AssessmentResult.scale_id, AssessmentResult.scale_version).in_(pairs))
        .order_by(AssessmentResult.scale_id, AssessmentResult.scale_version, AssessmentResult.id)
    )
    accepted = {(row.scale_id, row.scale_version): row.definition_snapshot for row in rows}
    for scale in scales:
        raise_if_conflicts(scale, accepted.get((scale.id, scale.version)))


def raise_if_conflicts(scale: Scale, accepted: dict[str, Any] | None) -> None:
    """503, если определение изменили без новой версии после первого принятого результата."""
    if accepted is None or accepted == definition_snapshot(scale):
        return
    log.error("assessments.definition_conflict", scale_id=scale.id, scale_version=scale.version)
    raise AssessmentError(
        503,
        "scale_definition_conflict",
        "Scale definition conflicts with an accepted snapshot",
    )
