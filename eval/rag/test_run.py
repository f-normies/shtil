"""Прогон ответов и сводка (спека #70, раздел 6)."""

from uuid import uuid4

import pytest
from clin_compass.modules.knowledge.schemas import Answer, Claim, FragmentRef

from dataset import Question
from run import (
    TokenRecorder,
    in_order,
    judge_sample,
    outcomes,
    record,
    summary,
    verdict_agreement,
)


def _question(qid: str, kind: str = "single") -> Question:
    return Question.model_validate(
        {
            "id": qid,
            "question": "Когда ЭЭГ?",
            "kind": kind,
            "split": "dev",
            "origin": [] if kind == "none" else ["a/1/1"],
            "relevant": [{"key": "a/1/1", "fingerprint": "0123456789ab", "grade": 2}]
            if kind != "none"
            else [],
        }
    )


def _claim(key: str) -> Claim:
    ref = FragmentRef(
        id=uuid4(),
        key=key,
        source_id="a",
        title="КР",
        version="1",
        section="1",
        section_title="Раздел",
        uur=None,
        udd=None,
        status="active",
    )
    return Claim(statement="ЭЭГ при приступах.", quote="Рекомендуется ЭЭГ.", fragment=ref)


def test_record_keeps_claims_for_judge() -> None:
    answer = Answer(claims=[_claim("a/1/1")], dropped=1, run_id=None)

    item = record(_question("q-001"), answer, latency_s=12.345, output_tokens=210)

    assert item == {
        "id": "q-001",
        "claims": [
            {"key": "a/1/1", "statement": "ЭЭГ при приступах.", "quote": "Рекомендуется ЭЭГ."}
        ],
        "dropped": 1,
        "latency_s": 12.35,
        "output_tokens": 210,
        "verdicts": None,
    }


def test_outcomes_join_records_with_questions_and_verdicts() -> None:
    questions = [_question("q-001"), _question("q-002", "none")]
    records = [
        {
            **record(
                questions[0],
                Answer(claims=[_claim("a/1/1")], dropped=0, run_id=None),
                latency_s=1,
                output_tokens=5,
            ),
            "verdicts": ["supported"],
        },
        record(
            questions[1], Answer(claims=[], dropped=0, run_id=None), latency_s=2, output_tokens=3
        ),
    ]

    joined = outcomes(records, questions)

    assert joined[0].cited == ("a/1/1",) and joined[0].verdicts == ("supported",)
    assert joined[1].cited == () and joined[1].verdicts == ()


def test_summary_shows_metrics_with_intervals_and_missing_judge() -> None:
    metrics = {
        "questions": 3,
        "quotes_found_share": {"mean": 0.9, "ci95": [0.8, 1.0]},
        "supported_share": None,
        "answered_share": None,
        "empty_on_none_share": {"mean": 1.0, "ci95": [1.0, 1.0]},
        "latency": {"median_s": 14.2, "p95_s": 30.1},
        "output_tokens_median": 180,
    }
    result = {
        "versions": {"llm": "qwen3.8-27b", "prompt": "knowledge.answer-1", "retrieval": "base"},
        "split": "dev",
        "metrics": metrics,
    }

    text = summary(result)

    assert "- Цитаты найдены: 0.9 [0.8; 1.0]." in text
    assert "- Подтверждённые утверждения: судья не запускался." in text
    assert "- Время ответа: медиана 14.2 с, p95 30.1 с; токенов ответа — медиана 180." in text


@pytest.mark.anyio
async def test_token_recorder_keeps_tokens_per_run() -> None:
    from datetime import UTC, datetime

    from clin_compass.core.ai.runs import RunRecord

    recorder = TokenRecorder()

    def run(tokens: int) -> RunRecord:
        now = datetime.now(UTC)
        return RunRecord(
            role="llm",
            task="t",
            model="m",
            node=None,
            status="ok",
            error=None,
            input_hash="h",
            params={},
            prompt_version="v",
            started_at=now,
            latency_ms=1,
            input_tokens=1,
            output_tokens=tokens,
            audio_seconds=None,
        )

    first = await recorder.record(run(10))
    second = await recorder.record(run(20))

    assert recorder.tokens[first] == 10 and recorder.tokens[second] == 20


@pytest.mark.anyio
async def test_in_order_keeps_order_and_limits_parallel_calls() -> None:
    import asyncio

    running = 0
    peak = 0

    async def work(n: int) -> int:
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.01 * (5 - n))
        running -= 1
        return n * 10

    assert await in_order(range(5), work, concurrency=2) == [0, 10, 20, 30, 40]
    assert peak == 2


def test_verdict_agreement_counts_same_verdict_and_supported_or_not() -> None:
    pairs = [
        ("supported", "supported"),
        ("partial", "unsupported"),
        ("supported", "partial"),
        ("unsupported", "unsupported"),
    ]

    assert verdict_agreement(pairs) == {"pairs": 4, "exact": 0.5, "supported": 0.75}
    assert verdict_agreement([]) == {"pairs": 0, "exact": None, "supported": None}


def test_judge_sample_takes_judged_records_with_claims_reproducibly() -> None:
    judged = {
        "id": "q-001",
        "claims": [{"key": "a/1/1", "statement": "s", "quote": "q"}],
        "verdicts": ["supported"],
    }
    runs = {
        "a.json": [judged, {**judged, "claims": [], "verdicts": []}, {**judged, "verdicts": None}],
        "b.json": [judged, judged],
    }

    first = judge_sample(runs, count=2, seed=70)

    assert first == judge_sample(runs, count=2, seed=70)
    assert len(first) == 2
    assert set(first) <= {("a.json", 0), ("b.json", 0), ("b.json", 1)}
    assert len(judge_sample(runs, count=10, seed=70)) == 3
