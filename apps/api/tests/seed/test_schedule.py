"""Даты записей сида и причины пропуска шкалы (спека #19, разделы 2 и 4). Без БД."""

from datetime import UTC, datetime

import pytest
from clin_compass_knowledge.loader import load_knowledge

from clin_compass.modules.assessments.service import ScaleCatalog
from clin_compass.seed.run import completed_at, skip_reason
from tests.assessments.conftest import KNOWLEDGE

CATALOG = ScaleCatalog.from_knowledge(load_knowledge(KNOWLEDGE))


@pytest.mark.parametrize(
    ("now", "days_ago", "expected"),
    [
        # 10:00 по Москве (UTC+3) в день «N дней назад» по московскому календарю.
        (datetime(2026, 10, 18, 12, 0, tzinfo=UTC), 10, datetime(2026, 10, 8, 7, 0, tzinfo=UTC)),
        # 22:30 UTC — в Москве уже 19.10: «вчера» — это 18.10.
        (datetime(2026, 10, 18, 22, 30, tzinfo=UTC), 1, datetime(2026, 10, 18, 7, 0, tzinfo=UTC)),
    ],
)
def test_record_is_filled_at_ten_moscow_time(
    now: datetime, days_ago: int, expected: datetime
) -> None:
    assert completed_at(now, days_ago) == expected


@pytest.mark.parametrize(
    ("scale_id", "reason"),
    [
        ("test-eligible", None),
        ("no-such-scale", "нет в knowledge/"),
        ("test-draft", "статус draft, нужен reviewed"),
        ("test-unlicensed", "лицензия не разрешает выдачу пациенту"),
        ("test-clinician", "шкалу заполняет врач"),
    ],
)
def test_skip_reason_names_why_a_scale_is_unavailable(scale_id: str, reason: str | None) -> None:
    assert skip_reason(CATALOG, scale_id) == reason
