"""Направление к психиатру без БД (спека #35, разделы 3 и 6)."""

from uuid import uuid4

import pytest

from clin_compass.modules.alerts.lifecycle import ConflictError, InvalidError
from clin_compass.modules.alerts.referral import check_decline, check_seen, decline_comment
from clin_compass.modules.alerts.rules import RuleBook
from tests.alerts.conftest import KNOWLEDGE

DECISIONS = RuleBook.load(KNOWLEDGE).decisions


@pytest.mark.parametrize(
    ("reason_id", "comment", "error"),
    [
        ("other", "Пациент переезжает в другой город", None),
        # Данные медика #93: комментарий к отказу обязателен при любой причине.
        ("other", "   ", InvalidError),
        ("other", None, InvalidError),
        (None, "Комментарий", InvalidError),
        ("no-such-reason", "Комментарий", InvalidError),
    ],
)
def test_decline_needs_a_listed_reason_and_a_comment(
    reason_id: str | None, comment: str | None, error: type[Exception] | None
) -> None:
    if error is None:
        assert check_decline(DECISIONS, reason_id, comment).id == reason_id
    else:
        with pytest.raises(error):
            check_decline(DECISIONS, reason_id, comment)


def test_decline_without_decision_data_is_a_conflict() -> None:
    with pytest.raises(ConflictError):
        check_decline(None, "other", "Комментарий")


def test_decline_comment_keeps_reason_and_doctor_words() -> None:
    assert decline_comment("Пациент отказался от консультации", "  обсудим через месяц ") == (
        "Пациент отказался от консультации. обсудим через месяц"
    )


def test_doctor_decides_only_on_signals_shown_in_the_sheet() -> None:
    # Спека #35, раздел 3: пришёл новый сигнал или старый решили — 409.
    a, b = uuid4(), uuid4()

    check_seen([a, b], [b, a])
    with pytest.raises(ConflictError):
        check_seen([a, b], [a])
    with pytest.raises(ConflictError):
        check_seen([a], [a, b])
    with pytest.raises(ConflictError, match="уже рассмотрен"):
        check_seen([], [a])
