"""Факты сводки из данных периода (спека #38, 3.2): шкалы, сигналы, находки дневника и чата.

Табличные тесты на публичных схемах модулей-источников, без базы. Даты — по Москве.
"""

from datetime import date, datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from clin_compass.modules.alerts.schemas import ActionKind, ActionOut, AlertOut
from clin_compass.modules.assessments.schemas import AssessmentOut
from clin_compass.modules.freetext.schemas import FindingOut
from clin_compass.modules.summary.facts import collect
from clin_compass.modules.summary.schemas import SummaryFact
from tests.summary.builders import alert, finding, result

MSK = ZoneInfo("Europe/Moscow")


def at(day: int, hour: int = 10) -> datetime:
    return datetime(2026, 10, day, hour, tzinfo=MSK)


START = at(10)


def facts(
    results: list[AssessmentOut] | None = None,
    alerts: list[AlertOut] | None = None,
    findings: list[FindingOut] | None = None,
) -> list[SummaryFact]:
    return collect(
        start=START,
        results=results or [],
        alerts=alerts or [],
        findings=findings or [],
        written={},
        zone=MSK,
    )


@pytest.mark.parametrize(
    ("results", "text"),
    [
        pytest.param(
            [
                result(at(4), 19, "Умеренно тяжёлая депрессия"),
                result(at(18), 16, "Умеренная депрессия"),
            ],
            "PHQ-9 18.10: 16 баллов, умеренная депрессия. К прошлому результату 04.10 "
            "(19 баллов, умеренно тяжёлая депрессия): −3.",
            id="изменение к результату до периода",
        ),
        pytest.param(
            [result(at(18), 16, "Умеренная депрессия")],
            "PHQ-9 18.10: 16 баллов, умеренная депрессия. Первое заполнение шкалы.",
            id="первый результат",
        ),
        pytest.param(
            [result(at(4), 21, "Тяжёлая депрессия"), result(at(18), 21, "Тяжёлая депрессия")],
            "PHQ-9 18.10: 21 балл, тяжёлая депрессия. К прошлому результату 04.10 "
            "(21 балл, тяжёлая депрессия): без изменений.",
            id="без изменений",
        ),
        pytest.param(
            [result(at(4), 2, "Минимальная депрессия"), result(at(18), 4, "Минимальная депрессия")],
            "PHQ-9 18.10: 4 балла, минимальная депрессия. К прошлому результату 04.10 "
            "(2 балла, минимальная депрессия): +2.",
            id="рост",
        ),
        pytest.param(
            [result(at(18), 1, "Норма", scale_id="test", title="Шкала без сокращения")],
            "Шкала без сокращения 18.10: 1 балл, норма. Первое заполнение шкалы.",
            id="название без сокращения",
        ),
    ],
)
def test_scale_fact_has_score_band_and_change_to_previous_result(
    results: list[AssessmentOut], text: str
) -> None:
    [fact] = facts(results=results)

    assert (fact.kind, fact.section, fact.at, fact.text, fact.level) == (
        "scale",
        "assessments",
        at(18),
        text,
        None,
    )


def test_each_result_in_period_compares_with_the_one_before_it() -> None:
    results = [
        result(at(4), 19, "Умеренно тяжёлая депрессия"),
        result(at(12), 16, "Умеренная депрессия"),
        result(at(18), 11, "Умеренная депрессия"),
    ]

    first, second = facts(results=results)

    assert "К прошлому результату 04.10 (19 баллов" in first.text
    assert "К прошлому результату 12.10 (16 баллов, умеренная депрессия): −5." in second.text


def test_result_received_in_period_belongs_to_it_even_if_completed_earlier() -> None:
    # Заполнено офлайн до начала периода, на сервер пришло после: врач его ещё не видел.
    late = result(at(9), 16, "Умеренная депрессия", received=at(11))
    seen = result(at(8), 19, "Умеренно тяжёлая депрессия", received=at(8))

    [fact] = facts(results=[seen, late])

    assert fact.at == at(9)
    assert fact.text.startswith("PHQ-9 09.10: 16 баллов")


def test_scale_with_several_sums_gives_a_fact_per_sum() -> None:
    hads = result(
        at(18),
        0,
        "",
        scale_id="hads",
        title="Госпитальная шкала тревоги и депрессии (HADS)",
        scores={"anxiety": (11, "Клинически выраженная"), "depression": (7, "Норма")},
    )

    texts = [f.text for f in facts(results=[hads])]

    assert texts == [
        "HADS, anxiety 18.10: 11 баллов, клинически выраженная. Первое заполнение шкалы.",
        "HADS, depression 18.10: 7 баллов, норма. Первое заполнение шкалы.",
    ]


@pytest.mark.parametrize(
    ("signal", "fact_at", "text"),
    [
        pytest.param(
            alert(at(12), "Мысли о смерти в дневнике"),
            at(12),
            "Сигнал «обязательно»: Мысли о смерти в дневнике. Состояние: новый.",
            id="новый",
        ),
        pytest.param(
            alert(at(12), "PHQ-9: 15 баллов и больше", level="desirable", state="in_work"),
            at(12),
            "Сигнал «желательно»: PHQ-9: 15 баллов и больше. Состояние: в работе.",
            id="в работе",
        ),
        pytest.param(
            alert(
                at(12),
                "Тяжёлая депрессия",
                kind="routing",
                level="desirable",
                state="snoozed",
                snoozed_until=date(2026, 10, 25),
            ),
            at(12),
            "Сигнал о направлении к психиатру «желательно»: Тяжёлая депрессия. Отложен до 25.10.",
            id="отложен",
        ),
        pytest.param(
            alert(
                at(12),
                "Противопоказание",
                kind="safety",
                state="resolved",
                resolved=at(13),
                decision="Назначение отменено",
            ),
            at(12),
            "Предупреждение о безопасности «обязательно»: Противопоказание. "
            "Решение врача: Назначение отменено.",
            id="решён",
        ),
        pytest.param(
            alert(at(12), "Сигнал без решения", state="resolved", resolved=at(13)),
            at(12),
            "Сигнал «обязательно»: Сигнал без решения. Состояние: закрыт.",
            id="закрыт без текста решения",
        ),
        pytest.param(
            alert(at(3), "Повтор", state="in_work", last_triggered=at(15), occurrences=3),
            at(15),
            "Сигнал «обязательно»: Повтор. Состояние: в работе. Срабатываний: 3.",
            id="поднят до периода, сработал в периоде",
        ),
        pytest.param(
            alert(at(3), "Решён в периоде", state="resolved", resolved=at(14), decision="Связался"),
            at(14),
            "Сигнал «обязательно»: Решён в периоде. Решение врача: Связался.",
            id="поднят до периода, решён в периоде",
        ),
    ],
)
def test_signal_fact_has_level_word_reason_and_state(
    signal: AlertOut, fact_at: datetime, text: str
) -> None:
    [fact] = facts(alerts=[signal])

    assert (fact.kind, fact.section, fact.at, fact.text) == ("signal", "signals", fact_at, text)
    # Уровень — отдельным полем: кабинет не угадывает его по словам текста.
    assert fact.level == signal.level


def test_signal_without_events_in_period_is_not_a_fact() -> None:
    assert facts(alerts=[alert(at(3), "Старый сигнал", state="in_work")]) == []


@pytest.mark.parametrize("action", ["acknowledge", "snooze", "no_contact"])
def test_action_on_old_signal_belongs_to_current_period(action: ActionKind) -> None:
    signal = alert(
        at(3),
        "Старый сигнал",
        state="snoozed" if action == "snooze" else "in_work",
        snoozed_until=date(2026, 10, 25) if action == "snooze" else None,
    )
    signal = signal.model_copy(
        update={
            "actions": [
                ActionOut(
                    at=at(12),
                    actor_id=signal.doctor_id,
                    action=action,
                    decision_id=None,
                    decision_text=None,
                    reason=None,
                    snoozed_until=None,
                )
            ]
        }
    )

    [fact] = facts(alerts=[signal])

    assert fact.at == at(12)
    if action == "no_contact":
        assert "Не удалось связаться" in fact.text


@pytest.mark.parametrize(
    ("item", "section", "text"),
    [
        pytest.param(
            finding(at(12), "symptom", "плохой сон", "Плохо сплю."),
            "diary",
            "Дневник, симптом: плохой сон. Цитата: «Плохо сплю.»",
            id="симптом в дневнике",
        ),
        pytest.param(
            finding(
                at(12),
                "adherence",
                "пропуск утренней дозы",
                "забыла выпить Авиандр",
                source_type="message",
                drug="Авиандр",
                event="missed",
            ),
            "messages",
            "Чат, приём препарата (Авиандр, пропуск): пропуск утренней дозы. "
            "Цитата: «забыла выпить Авиандр»",
            id="пропуск приёма в чате",
        ),
        pytest.param(
            finding(
                at(12),
                "adherence",
                "бросила таблетки",
                "больше не пью",
                transcript=True,
                event="stopped",
            ),
            "diary",
            "Дневник, голосовое, приём препарата (прекращение приёма): бросила таблетки. "
            "Цитата: «больше не пью»",
            id="голосовое без названия препарата",
        ),
        pytest.param(
            finding(at(12), "side_effect", "головная боль", "болит голова"),
            "diary",
            "Дневник, нежелательное явление: головная боль. Цитата: «болит голова»",
            id="нежелательное явление",
        ),
        pytest.param(
            finding(at(12), "medication", "пьёт глицин", "пью глицин", drug="Глицин"),
            "diary",
            "Дневник, другой препарат (Глицин): пьёт глицин. Цитата: «пью глицин»",
            id="другой препарат",
        ),
    ],
)
def test_finding_fact_has_record_type_category_summary_and_quote(
    item: FindingOut, section: str, text: str
) -> None:
    [fact] = facts(findings=[item])

    assert (fact.kind, fact.section, fact.text, fact.level) == ("finding", section, text, None)


def test_finding_date_is_when_patient_wrote_it() -> None:
    analysis_id = uuid4()
    item = finding(at(20), "symptom", "плохой сон", "Плохо сплю.", analysis_id=analysis_id)

    [fact] = collect(
        start=START,
        results=[],
        alerts=[],
        findings=[item],
        written={analysis_id: at(17)},
        zone=MSK,
    )

    assert fact.at == at(17)


def test_finding_confirmed_before_period_is_not_a_fact() -> None:
    assert facts(findings=[finding(at(9), "symptom", "плохой сон", "Плохо сплю.")]) == []


def test_facts_are_numbered_in_date_order_across_kinds() -> None:
    numbered = facts(
        results=[result(at(18), 16, "Умеренная депрессия")],
        alerts=[alert(at(12), "Сигнал"), alert(at(18), "Сигнал в тот же час")],
        findings=[finding(at(15), "symptom", "плохой сон", "Плохо сплю.")],
    )

    assert [(f.number, f.kind) for f in numbered] == [
        (1, "signal"),
        (2, "finding"),
        (3, "scale"),
        (4, "signal"),
    ]
