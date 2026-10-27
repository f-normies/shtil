"""Временный доступ заместителя без БД (спека #56, раздел 3): действует, пока не закрыт
и не истёк."""

from datetime import UTC, datetime, timedelta

import pytest

from clin_compass.modules.patients.access import grant_active

NOW = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("ended_at", "expires_at", "expected"),
    [
        (None, NOW + timedelta(minutes=1), True),
        (None, NOW, False),  # истёк ровно сейчас
        (None, NOW - timedelta(hours=1), False),
        (NOW - timedelta(minutes=5), NOW + timedelta(hours=1), False),  # закрыт решением
    ],
)
def test_grant_is_active_until_it_ends_or_expires(
    ended_at: datetime | None, expires_at: datetime, expected: bool
) -> None:
    assert grant_active(ended_at, expires_at, NOW) is expected
