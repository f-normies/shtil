"""Время ответа на вопрос врача к базе знаний на стенде (спека #108, раздел 3.4).

    read -rs COMPASS_DOCTOR_PASSWORD && export COMPASS_DOCTOR_PASSWORD
    uv run --package clin-compass-eval python eval/rag/latency.py \\
        --base-url https://<стенд> --email <почта врача> [--limit 20]

Скрипт входит врачом (`POST /api/v1/auth/login`, дальше — cookie сессии) и задаёт первые
20 вопросов контрольной половины набора v3 с ответом в базе (`split: test`, у вопроса есть
пункты эталона) через `POST /api/v1/knowledge/answers`. Запрос тот же, что у кнопки
«Спросить по КР»: только вопрос, без `source_ids` и без сведений о пациенте. Запросы идут
по одному, время — от отправки до полного ответа. Медиана, p95 и максимум считаются по
отвеченным вопросам (200 с разобранным телом). Бюджет выполнен, только если ошибок нет
(сеть, 502, 503, тело не разобрать) и p95 не больше 15 с. Ошибка вопроса не прерывает замер.
Каждый вопрос пишется в `ai_runs` стенда.

Пароль берётся только из переменной `COMPASS_DOCTOR_PASSWORD` и никуда не пишется.
Результат — JSON в `eval/rag/results/` без текста ответов (в нём были бы цитаты КР),
на экран — сводка. Цифры переносятся в `docs/requirements.md` (метрика 1).
"""

import argparse
import json
import os
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from dataset_v3 import QuestionV3, load_questions_v3
from rag_metrics import latency

RESULTS = Path(__file__).resolve().parent / "results"
PASSWORD_ENV = "COMPASS_DOCTOR_PASSWORD"
LOGIN = "/api/v1/auth/login"
ANSWERS = "/api/v1/knowledge/answers"
LIMIT = 20
# Бюджет кнопки «Спросить по КР»: p95 не больше 15 с (спека #108, критерии приёмки).
BUDGET_P95_S = 15.0
# Ручка ждёт модель до 180 с; запас на сеть до стенда.
TIMEOUT_S = 200.0


class LoginError(RuntimeError):
    """Стенд не впустил врача. В тексте только код ответа: пароль не попадает в вывод."""


# Тело ответа 200 не JSON или без списка `claims`: например, страница шлюза.
BAD_BODY = "тело ответа не разобрать"


@dataclass(frozen=True)
class Timing:
    """Один вопрос: код ответа (None — сбой сети), время, число утверждений и ошибка.

    Вопрос отвечен, если код 200 и ошибки нет; тогда `claims` — число утверждений.
    """

    id: str
    status: int | None
    seconds: float
    claims: int | None
    error: str | None = None

    @property
    def answered(self) -> bool:
        return self.status == 200 and self.error is None


def stand_questions(questions: Sequence[QuestionV3], limit: int = LIMIT) -> list[QuestionV3]:
    """Первые `limit` вопросов контрольной половины (`test`) с ответом в базе, в порядке набора."""
    chosen = [q for q in questions if q.split == "test" and q.kind != "none"]
    return chosen[:limit]


def login(client: httpx.Client, email: str, password: str) -> None:
    """Вход врача: cookie сессии остаётся в `client`.

    Raises:
        LoginError: Стенд ответил не 200.
    """
    response = client.post(LOGIN, json={"email": email, "password": password})
    if response.status_code != 200:
        raise LoginError(f"вход не удался: {response.status_code}")


def ask_all(
    client: httpx.Client,
    questions: Sequence[QuestionV3],
    clock: Callable[[], float] = time.perf_counter,
) -> list[Timing]:
    """Задаёт вопросы по одному и меряет время каждого.

    Сбой одного вопроса — сеть, код не 200, тело не разобрать — записывается ошибкой
    и не прерывает замер: файл результата пишется всегда.
    """
    timings = []
    for question in questions:
        started = clock()
        try:
            response = client.post(ANSWERS, json={"question": question.question})
        except httpx.HTTPError as exc:
            seconds = round(clock() - started, 2)
            timings.append(Timing(question.id, None, seconds, None, f"сеть: {type(exc).__name__}"))
            continue
        seconds = round(clock() - started, 2)
        status = response.status_code
        if status != 200:
            timings.append(Timing(question.id, status, seconds, None, f"HTTP {status}"))
            continue
        try:
            claims = len(response.json()["claims"])
        except (ValueError, KeyError, TypeError):
            timings.append(Timing(question.id, status, seconds, None, BAD_BODY))
            continue
        timings.append(Timing(question.id, status, seconds, claims))
    return timings


def summarize(timings: Sequence[Timing]) -> dict[str, Any]:
    """Сколько отвечено и с ошибкой, пустые ответы, медиана, p95 и максимум по отвеченным.

    Уложились — только если ошибок нет и p95 не больше 15 с. Упавший вопрос (таймаут модели,
    узел недоступен) врач тоже ждал, по одним отвеченным бюджет не проверить.
    """
    answered = [t for t in timings if t.answered]
    seconds = [t.seconds for t in answered]
    measured = latency(seconds)
    if measured is not None:
        measured["max_s"] = round(max(seconds), 2)
    failed = len(timings) - len(answered)
    return {
        "answered": len(answered),
        "failed": failed,
        "empty": sum(1 for t in answered if t.claims == 0),
        "latency": measured,
        "budget_p95_s": BUDGET_P95_S,
        "within_budget": failed == 0 and measured is not None and measured["p95_s"] <= BUDGET_P95_S,
    }


def result(base_url: str, timings: Sequence[Timing], *, date: str) -> dict[str, Any]:
    """Запись замера для `results/`: стенд, дата, время каждого вопроса и сводка."""
    return {
        "date": date,
        "base_url": base_url,
        "endpoint": ANSWERS,
        "questions": [asdict(t) for t in timings],
        "summary": summarize(timings),
    }


def summary_text(data: Mapping[str, Any]) -> str:
    """Сводка на экран: число вопросов, ошибки, медиана, p95 и бюджет."""
    summary = data["summary"]
    measured = summary["latency"]
    timing = (
        f"медиана {measured['median_s']} с, p95 {measured['p95_s']} с, "
        f"максимум {measured['max_s']} с (по отвеченным)"
        if measured
        else "отвеченных нет"
    )
    if summary["within_budget"]:
        verdict = "уложились"
    elif summary["failed"]:
        verdict = f"не уложились: {_errors(summary['failed'])}"
    else:
        verdict = "не уложились: p95 больше бюджета"
    return "\n".join(
        [
            f"Стенд {data['base_url']}, {data['date']}: вопросов {len(data['questions'])}",
            f"- Отвечено: {summary['answered']}, из них пустых {summary['empty']}; "
            f"ошибок: {summary['failed']}",
            f"- Время ответа: {timing}",
            f"- Бюджет p95 {summary['budget_p95_s']} с — {verdict}",
        ]
    )


def _errors(count: int) -> str:
    """«1 ошибка», «3 ошибки», «5 ошибок»."""
    if count % 10 == 1 and count % 100 != 11:
        return f"{count} ошибка"
    if count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
        return f"{count} ошибки"
    return f"{count} ошибок"


def main(argv: Sequence[str] | None = None) -> int:
    """Вход, вопросы по одному, JSON в `results/` и сводка на экран."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", required=True, help="адрес стенда, https://…")
    parser.add_argument("--email", required=True, help="почта врача на стенде")
    parser.add_argument("--limit", type=int, default=LIMIT, help="сколько вопросов задать")
    args = parser.parse_args(argv)
    password = os.environ.get(PASSWORD_ENV)
    if not password:
        print(f"Задайте пароль врача в переменной {PASSWORD_ENV}", file=sys.stderr)  # noqa: T201
        return 2

    questions = stand_questions(load_questions_v3(), args.limit)
    with httpx.Client(base_url=args.base_url, timeout=TIMEOUT_S) as client:
        try:
            login(client, args.email, password)
        except (LoginError, httpx.HTTPError) as exc:
            print(f"{args.base_url}: {exc}", file=sys.stderr)  # noqa: T201
            return 1
        timings = ask_all(client, questions)

    now = datetime.now(UTC)
    data = result(args.base_url, timings, date=now.date().isoformat())
    RESULTS.mkdir(exist_ok=True)
    path = RESULTS / f"latency-{now:%Y-%m-%d-%H%M}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    print(summary_text(data))  # noqa: T201
    print(f"\nРезультат: {path}")  # noqa: T201
    return 0


if __name__ == "__main__":
    sys.exit(main())
