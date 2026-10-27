"""Экран помощи пациенту в knowledge/ этого репозитория (спека #57)."""

from clin_compass_knowledge.loader import load_knowledge
from tests.helpers import REPO_KNOWLEDGE

KNOWLEDGE = load_knowledge(REPO_KNOWLEDGE)


def test_repository_has_reviewed_patient_help() -> None:
    # Загрузчик отсутствие файла пропускает; в репозитории он обязателен (#57).
    help_ = KNOWLEDGE.patient_help

    assert help_ is not None
    assert help_.status == "reviewed"


def test_contacts_follow_the_figma_frame() -> None:
    # Кадр «Пациент · 26» и issue #57: главная кнопка — 112, вторая — скорая 103.
    # Запасной номер PWA (apps/patient/src/help) совпадает с главным контактом.
    help_ = KNOWLEDGE.patient_help
    assert help_ is not None

    assert [(c.number, c.primary) for c in help_.contacts] == [("112", True), ("103", False)]
