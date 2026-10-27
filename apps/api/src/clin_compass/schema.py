"""Метаданные всех таблиц для Alembic. Новый модуль с моделями добавляет сюда свой импорт."""

from clin_compass.core.ai import runs as ai_runs
from clin_compass.core.orm import Base
from clin_compass.modules.adherence import models as adherence_models
from clin_compass.modules.alerts import models as alerts_models
from clin_compass.modules.assessments import models as assessments_models
from clin_compass.modules.diary import models as diary_models
from clin_compass.modules.freetext import models as freetext_models
from clin_compass.modules.identity import models as identity_models
from clin_compass.modules.ingestion import models as ingestion_models
from clin_compass.modules.knowledge import models as knowledge_models
from clin_compass.modules.messaging import models as messaging_models
from clin_compass.modules.patients import models as patients_models
from clin_compass.modules.proposals import models as proposals_models
from clin_compass.modules.summary import models as summary_models
from clin_compass.modules.therapy import models as therapy_models

metadata = Base.metadata

__all__ = [
    "adherence_models",
    "ai_runs",
    "alerts_models",
    "assessments_models",
    "diary_models",
    "freetext_models",
    "identity_models",
    "ingestion_models",
    "knowledge_models",
    "messaging_models",
    "metadata",
    "patients_models",
    "proposals_models",
    "summary_models",
    "therapy_models",
]
