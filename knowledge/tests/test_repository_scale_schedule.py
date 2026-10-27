"""Расписание шкал в knowledge/ этого репозитория (спека #111)."""

from clin_compass_knowledge.loader import load_knowledge
from tests.helpers import REPO_KNOWLEDGE

KNOWLEDGE = load_knowledge(REPO_KNOWLEDGE)


def test_repository_has_reviewed_scale_schedule() -> None:
    # Загрузчик отсутствие файла пропускает; в репозитории он обязателен (#31, #99).
    schedule = KNOWLEDGE.scale_schedule

    assert schedule is not None
    assert schedule.status == "reviewed"


def test_phq9_and_gad7_every_14_days_missed_after_3() -> None:
    # Ответ медика в #93, пункт 4: обе шкалы спрашивают о прошедших двух неделях.
    schedule = KNOWLEDGE.scale_schedule

    assert schedule is not None
    assert schedule.grace_days == 3
    assert {s.scale: s.period_days for s in schedule.scales} == {"phq-9": 14, "gad-7": 14}
