"""Замер времени ответа на стенде (спека #108, 3.4): вопросы, вход, запросы по одному, сводка.

Сети нет: стенд изображает `httpx.MockTransport`.
"""

import json
from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from dataset_v3 import load_questions_v3
from latency import (
    BUDGET_P95_S,
    LoginError,
    Timing,
    ask_all,
    login,
    result,
    stand_questions,
    summarize,
    summary_text,
)
from test_v3_metrics import _none, _question

ANSWERS = "/api/v1/knowledge/answers"
LOGIN = "/api/v1/auth/login"
SECRET = "пароль-стенда"
CLAIM = {"statement": "у", "quote": "цитата КР", "fragment": {"id": "f"}}


def _clock(*values: float) -> Iterator[float]:
    return iter(values)


def _stand(replies: list[httpx.Response | Exception]) -> tuple[httpx.Client, list[httpx.Request]]:
    """Стенд: вход ставит cookie сессии, ответы на вопросы — по очереди из `replies`."""
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == LOGIN:
            body = json.loads(request.content)
            if body["password"] != SECRET:
                return httpx.Response(401, json={"status": 401, "title": "Неверный вход"})
            return httpx.Response(200, json={}, headers={"set-cookie": "session=s1; Path=/"})
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    client = httpx.Client(transport=httpx.MockTransport(handle), base_url="https://stand.invalid")
    return client, seen


def test_stand_questions_are_first_control_questions_with_answer() -> None:
    questions = [
        _question("d-1"),
        _none("t-none"),
        _question("t-1", split="test"),
        _question("t-2", split="test"),
        _question("t-3", split="test"),
    ]

    chosen = stand_questions(questions, limit=2)

    assert [q.id for q in chosen] == ["t-1", "t-2"]


def test_v3_set_gives_twenty_control_questions_with_points() -> None:
    chosen = stand_questions(load_questions_v3())

    assert len(chosen) == 20
    assert {q.split for q in chosen} == {"test"}
    assert all(q.points for q in chosen)


def test_doctor_logs_in_and_questions_go_one_by_one_with_the_session_cookie() -> None:
    client, seen = _stand([httpx.Response(200, json={"claims": [CLAIM], "dropped": 0})] * 2)
    questions = [_question("t-1", split="test", question="Первый?"), _question("t-2", split="test")]

    login(client, "doctor@example.invalid", SECRET)
    timings = ask_all(client, questions, clock=_clock(0.0, 9.5, 10.0, 22.25).__next__)

    assert [r.url.path for r in seen] == [LOGIN, ANSWERS, ANSWERS]
    assert json.loads(seen[0].content) == {"email": "doctor@example.invalid", "password": SECRET}
    # Только вопрос, как у кнопки «Спросить по КР»: без source_ids и сведений о пациенте.
    assert json.loads(seen[1].content) == {"question": "Первый?"}
    assert all(r.headers["cookie"] == "session=s1" for r in seen[1:])
    assert timings == [
        Timing(id="t-1", status=200, seconds=9.5, claims=1),
        Timing(id="t-2", status=200, seconds=12.25, claims=1),
    ]


def test_failed_login_names_no_password() -> None:
    client, _ = _stand([])

    with pytest.raises(LoginError) as error:
        login(client, "doctor@example.invalid", "чужой-пароль")

    assert "401" in str(error.value)
    assert "чужой-пароль" not in str(error.value)


def test_errors_are_recorded_and_left_out_of_latency() -> None:
    client, _ = _stand(
        [
            httpx.Response(502, json={"status": 502}),
            httpx.Response(503, json={"status": 503}),
            httpx.ConnectError("нет связи"),
            httpx.Response(200, json={"claims": [], "dropped": 0}),
        ]
    )
    questions = [_question(f"t-{i}", split="test") for i in range(4)]

    timings = ask_all(client, questions, clock=_clock(0, 1, 2, 3, 4, 5, 6, 13).__next__)

    assert [(t.status, t.claims, t.error) for t in timings] == [
        (502, None, "HTTP 502"),
        (503, None, "HTTP 503"),
        (None, None, "сеть: ConnectError"),
        (200, 0, None),
    ]
    summary = summarize(timings)
    assert (summary["answered"], summary["failed"], summary["empty"]) == (1, 3, 1)
    assert summary["latency"] == {"median_s": 7.0, "p95_s": 7.0, "max_s": 7.0}


def test_failed_questions_break_the_budget_even_with_fast_answers() -> None:
    # Упавшие вопросы (таймаут 502, 503, сеть) не дают сказать «уложились» по одним ответам 200.
    timings = [
        *(Timing(id=f"t-{i}", status=200, seconds=9.0, claims=1) for i in range(19)),
        Timing(id="t-19", status=502, seconds=180.0, claims=None, error="HTTP 502"),
    ]

    summary = summarize(timings)

    assert summary["latency"] == {"median_s": 9.0, "p95_s": 9.0, "max_s": 9.0}
    assert summary["within_budget"] is False
    text = summary_text(result("https://stand.invalid", timings, date="2026-10-23"))
    assert "Бюджет p95 15.0 с — не уложились: 1 ошибка" in text
    assert "p95 9.0 с" in text


@pytest.mark.parametrize(
    ("count", "word"), [(1, "ошибка"), (3, "ошибки"), (5, "ошибок"), (11, "ошибок")]
)
def test_error_count_is_spelled_out(count: int, word: str) -> None:
    timings = [Timing(id=f"t-{i}", status=503, seconds=0.1, claims=None) for i in range(count)]

    text = summary_text(result("https://stand.invalid", timings, date="2026-10-23"))

    assert f"не уложились: {count} {word}" in text


def test_broken_200_body_is_an_error_and_the_run_goes_on() -> None:
    client, _ = _stand(
        [
            httpx.Response(200, text="<html>шлюз</html>"),
            httpx.Response(200, json={"detail": "нет поля claims"}),
            httpx.Response(200, json={"claims": None}),
            httpx.Response(200, json={"claims": [CLAIM], "dropped": 0}),
        ]
    )
    questions = [_question(f"t-{i}", split="test") for i in range(4)]

    timings = ask_all(client, questions, clock=_clock(0, 1, 2, 3, 4, 5, 6, 8).__next__)

    assert [(t.status, t.claims, t.error) for t in timings] == [
        (200, None, "тело ответа не разобрать"),
        (200, None, "тело ответа не разобрать"),
        (200, None, "тело ответа не разобрать"),
        (200, 1, None),
    ]
    summary = summarize(timings)
    assert (summary["answered"], summary["failed"]) == (1, 3)
    assert summary["within_budget"] is False


def test_summary_gives_median_p95_and_the_15_s_budget() -> None:
    fast = [Timing(id=f"t-{i}", status=200, seconds=float(i), claims=1) for i in range(1, 21)]
    slow = [*fast[:-1], Timing(id="t-20", status=200, seconds=40.0, claims=1)]

    assert BUDGET_P95_S == 15.0
    assert summarize(fast)["latency"] == {"median_s": 10.5, "p95_s": 19.0, "max_s": 20.0}
    assert summarize(fast)["within_budget"] is False
    assert summarize(fast[:14])["within_budget"] is True
    assert summarize(slow)["latency"] == {"median_s": 10.5, "p95_s": 19.0, "max_s": 40.0}


def test_nothing_answered_is_not_within_budget() -> None:
    summary = summarize([Timing(id="t-1", status=503, seconds=0.1, claims=None)])

    assert summary["latency"] is None
    assert summary["within_budget"] is False


def test_result_keeps_no_answer_text_and_the_summary_names_the_numbers() -> None:
    timings = [Timing(id="t-1", status=200, seconds=9.6, claims=2)]

    data: dict[str, Any] = result("https://stand.invalid", timings, date="2026-10-23")
    text = json.dumps(data, ensure_ascii=False)

    assert data["questions"] == [
        {"id": "t-1", "status": 200, "seconds": 9.6, "claims": 2, "error": None}
    ]
    assert "цитата" not in text
    assert SECRET not in text
    lines = summary_text(data)
    assert "медиана 9.6 с, p95 9.6 с, максимум 9.6 с" in lines
    assert "Бюджет p95 15.0 с — уложились" in lines
