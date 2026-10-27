"""Прогон ответов RAG и их оценка судьёй (спека #70, раздел 6).

    uv run --package clin-compass-eval python eval/rag/run.py answer --database-url … \\
        --llm-url http://<адрес>:8000 [--llm-model <имя>] [--retrieval rerank=bge-v2-m3@20] \\
        [--citation quote|sentences] [--fragments 5] [--claims 5] [--split dev] --tag <метка>
    uv run --package clin-compass-eval python eval/rag/run.py judge --llm-url … \\
        [--llm-model <имя>] [--concurrency 2] --answers <файл>
    uv run --package clin-compass-eval python eval/rag/run.py judge-agree --llm-url … \\
        --llm-model <второй судья> --answers <файл> [<файл> …] [--count 60]
    uv run --package clin-compass-eval python eval/rag/run.py compare --answers <файл> … \\
        --tag <метка>

`answer` берёт выдачу поиска из конфигурации подбора (`--retrieval`, имя из `tune.py grid`),
отвечает `KnowledgeAi` и пишет сырые итоги: утверждения с цитатами, отброшенные,
время и число токенов ответа. Задержка — только ответ модели: поиск идёт из кэша подбора.
`judge` запускает судью утверждений и дописывает вердикты и метрики раздела 4.
`judge-agree` — сверка судьи со вторым: случайные ответы с вердиктами судит вторая модель,
согласие пишется в `results/judge-claims-agreement-<дата>.json`.
`compare` сравнивает прогоны с вердиктами по правилу 6.5: какие равны лучшему по обеим
главным метрикам.
"""

import argparse
import asyncio
import json
import random
import sys
import time
from collections.abc import Awaitable, Callable, Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from clin_compass.core.ai.llm import MODEL_ALIAS, Llm, build_llm_model
from clin_compass.core.ai.runs import NullRecorder, RunRecord
from clin_compass.core.config import Settings
from clin_compass.core.db import make_engine, make_sessionmaker
from clin_compass.modules.knowledge import service
from clin_compass.modules.knowledge.answering import CITATION, MAX_CLAIMS, MAX_FRAGMENTS
from clin_compass.modules.knowledge.schemas import Answer, Hit

from dataset import QUESTIONS, Question, check_index, fingerprint, load_questions
from judge import ClaimJudge
from rag_metrics import AnswerOutcome, answer_summary, compare_runs
from retrieval import Config
from tune import _engine

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"


class TokenRecorder(NullRecorder):
    """Запоминает число токенов ответа каждого вызова LLM по его `run_id`."""

    def __init__(self) -> None:
        self.tokens: dict[UUID, int] = {}

    async def record(self, run: RunRecord) -> UUID:
        run_id = uuid4()
        self.tokens[run_id] = run.output_tokens or 0
        return run_id


async def in_order[T, R](
    items: Iterable[T], fn: Callable[[T], Awaitable[R]], *, concurrency: int
) -> list[R]:
    """Результаты `fn` по порядку `items`; одновременно не больше `concurrency` вызовов."""
    gate = asyncio.Semaphore(concurrency)

    async def one(item: T) -> R:
        async with gate:
            return await fn(item)

    return list(await asyncio.gather(*(one(item) for item in items)))


def record(
    question: Question, answer: Answer, *, latency_s: float, output_tokens: int
) -> dict[str, Any]:
    """Сырой итог ответа на вопрос: утверждения нужны судье, текст фрагментов — нет."""
    return {
        "id": question.id,
        "claims": [
            {"key": c.fragment.key, "statement": c.statement, "quote": c.quote}
            for c in answer.claims
        ],
        "dropped": answer.dropped,
        "latency_s": round(latency_s, 2),
        "output_tokens": output_tokens,
        "verdicts": None,
    }


def outcomes(
    records: Sequence[dict[str, Any]], questions: Sequence[Question]
) -> list[AnswerOutcome]:
    """Итоги для метрик: записи прогона, сведённые с вопросами набора."""
    by_id = {q.id: q for q in questions}
    return [
        AnswerOutcome(
            by_id[r["id"]],
            cited=tuple(c["key"] for c in r["claims"]),
            verdicts=tuple(r["verdicts"])
            if r["verdicts"] is not None
            else (None,) * len(r["claims"]),
            dropped=r["dropped"],
            latency_s=r["latency_s"],
            output_tokens=r["output_tokens"],
        )
        for r in records
    ]


def verdict_agreement(pairs: Sequence[tuple[str, str]]) -> dict[str, Any]:
    """Согласие двух судей по вердиктам одного утверждения.

    `exact` — тот же вердикт; `supported` — согласие в «подтверждено или нет».
    """
    if not pairs:
        return {"pairs": 0, "exact": None, "supported": None}
    n = len(pairs)
    return {
        "pairs": n,
        "exact": round(sum(a == b for a, b in pairs) / n, 3),
        "supported": round(sum((a == "supported") == (b == "supported") for a, b in pairs) / n, 3),
    }


def judge_sample(
    runs: dict[str, list[dict[str, Any]]], *, count: int, seed: int
) -> list[tuple[str, int]]:
    """Случайные ответы для сверки судей: `(файл прогона, номер записи)`.

    Берутся только записи с утверждениями и вердиктами первого судьи.
    """
    judged = [
        (name, n)
        for name, records in runs.items()
        for n, r in enumerate(records)
        if r["claims"] and r["verdicts"] is not None
    ]
    return random.Random(seed).sample(judged, min(count, len(judged)))


def _share(item: dict[str, Any] | None) -> str:
    if item is None:
        return "судья не запускался"
    low, high = item["ci95"]
    return f"{item['mean']} [{low}; {high}]"


def summary(result: dict[str, Any]) -> str:
    """Сводка прогона для results/ и docs/requirements.md."""
    m = result["metrics"]
    v = result["versions"]
    latency = m["latency"] or {"median_s": "—", "p95_s": "—"}
    return "\n".join(
        [
            f"LLM `{v['llm']}`, промпт `{v['prompt']}`, поиск `{v['retrieval']}`, "
            f"часть `{result['split']}`, вопросов {m['questions']}.",
            "",
            f"- Цитаты найдены: {_share(m['quotes_found_share'])}.",
            f"- Подтверждённые утверждения: {_share(m['supported_share'])}.",
            f"- Вопросы с подтверждённым ответом: {_share(m['answered_share'])}.",
            f"- Пустой ответ на вопросы без ответа в базе: {_share(m['empty_on_none_share'])}.",
            f"- Время ответа: медиана {latency['median_s']} с, p95 {latency['p95_s']} с; "
            f"токенов ответа — медиана {m['output_tokens_median']}.",
        ]
    )


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _log(message: str) -> None:
    print(message, file=sys.stderr)  # noqa: T201


async def cmd_answer(args: argparse.Namespace) -> int:
    questions = [q for q in load_questions(QUESTIONS) if q.split == args.split][: args.limit]
    engine, _ = await _engine(args, strict=True)
    db = make_engine(args.database_url)
    try:
        async with make_sessionmaker(db)() as session:
            fragments = {f.key: f for f in await service.list_fragments(session)}
    finally:
        await db.dispose()
    problems = check_index(questions, {k: fingerprint(f.text) for k, f in fragments.items()})
    if problems:
        _log("Набор не совпадает с индексом:\n" + "\n".join(problems))
        return 1
    # Время ответа меряется отдельно; таймаут выше боевого, чтобы прогон не падал на выбросах.
    settings = Settings(
        database_url=args.database_url, llm_urls=(args.llm_url,), llm_timeout_seconds=600
    )
    recorder = TokenRecorder()
    llm = Llm(build_llm_model(settings, model=args.llm_model), recorder)
    ai = service.KnowledgeAi(
        None,  # type: ignore[arg-type]  # эмбеддинги не нужны: выдача из кэша подбора
        llm,
        citation=args.citation,
        max_fragments=args.fragments,
        max_claims=args.claims,
    )
    config = Config.from_name(args.retrieval)

    async def one(question: Question) -> dict[str, Any]:
        keys = engine.rank(config, question.question)[: args.fragments]
        hits = [
            Hit(fragment=fragments[k], score=0.0, vector_rank=None, text_rank=None) for k in keys
        ]
        started = time.perf_counter()
        answer = await ai.answer(question.question, hits)
        elapsed = time.perf_counter() - started
        tokens = recorder.tokens.get(answer.run_id, 0) if answer.run_id else 0
        _log(f"{question.id}: {len(answer.claims)} утв., {elapsed:.1f} с")
        return record(question, answer, latency_s=elapsed, output_tokens=tokens)

    # При --concurrency больше 1 время ответа меряется под нагрузкой: для замера времени
    # нужен отдельный прогон по одному запросу (--concurrency 1 --limit …).
    records = await in_order(questions, one, concurrency=args.concurrency)
    result = {
        "date": datetime.now(UTC).isoformat(timespec="seconds"),
        "split": args.split,
        "versions": {"llm": args.llm_model, "prompt": ai.prompt_version, "retrieval": config.name},
        "options": {
            "citation": args.citation,
            "fragments": args.fragments,
            "claims": args.claims,
            "concurrency": args.concurrency,
            "limit": args.limit,
        },
        "records": records,
    }
    result["metrics"] = answer_summary(outcomes(records, questions))
    path = RESULTS / f"answers-{datetime.now(UTC).date()}-{args.split}-{args.tag}.json"
    _write_json(path, result)
    print(summary(result))  # noqa: T201
    _log(f"Результат: {path}")
    return 0


def _claim_judge(args: argparse.Namespace) -> ClaimJudge:
    settings = Settings(
        database_url="postgresql+asyncpg://unused/unused",
        llm_urls=(args.llm_url,),
        llm_timeout_seconds=600,
    )
    return ClaimJudge(Llm(build_llm_model(settings, model=args.llm_model), NullRecorder()))


async def _verdicts(judge: ClaimJudge, question: Question, item: dict[str, Any]) -> list[str]:
    claims = [(c["statement"], c["quote"]) for c in item["claims"]]
    return await judge.verdicts(question.question, claims)


async def cmd_judge(args: argparse.Namespace) -> int:
    path = Path(args.answers)
    result = _read_json(path)
    questions = load_questions(QUESTIONS)
    by_id = {q.id: q for q in questions}
    judge = _claim_judge(args)
    done = 0

    async def one(item: dict[str, Any]) -> list[str]:
        nonlocal done
        verdicts = await _verdicts(judge, by_id[item["id"]], item)
        done += 1
        _log(f"судья: {done}/{len(result['records'])}")
        return verdicts

    for item, verdicts in zip(
        result["records"],
        await in_order(result["records"], one, concurrency=args.concurrency),
        strict=True,
    ):
        item["verdicts"] = verdicts
    result["judge"] = {"model": args.llm_model, "prompt": judge.prompt_version}
    result["metrics"] = answer_summary(outcomes(result["records"], questions))
    _write_json(path, result)
    print(summary(result))  # noqa: T201
    return 0


async def cmd_judge_agree(args: argparse.Namespace) -> int:
    runs = {Path(a).name: _read_json(Path(a)) for a in args.answers}
    by_id = {q.id: q for q in load_questions(QUESTIONS)}
    judge = _claim_judge(args)
    chosen = judge_sample(
        {name: r["records"] for name, r in runs.items()}, count=args.count, seed=args.seed
    )

    async def one(pick: tuple[str, int]) -> dict[str, Any]:
        name, n = pick
        item = runs[name]["records"][n]
        second = await _verdicts(judge, by_id[item["id"]], item)
        _log(f"{name} {item['id']}: {item['verdicts']} / {second}")
        return {
            "run": name,
            "id": item["id"],
            "claims": item["claims"],
            "first": item["verdicts"],
            "second": second,
        }

    records = await in_order(chosen, one, concurrency=args.concurrency)
    pairs = [(a, b) for r in records for a, b in zip(r["first"], r["second"], strict=True)]
    result = {
        "date": datetime.now(UTC).isoformat(timespec="seconds"),
        "first": sorted({json.dumps(r.get("judge"), ensure_ascii=False) for r in runs.values()}),
        "second": {"model": args.llm_model, "prompt": judge.prompt_version},
        "seed": args.seed,
        "agreement": verdict_agreement(pairs),
        "records": records,
    }
    path = RESULTS / f"judge-claims-agreement-{datetime.now(UTC).date()}.json"
    _write_json(path, result)
    print(json.dumps(result["agreement"], ensure_ascii=False))  # noqa: T201
    _log(f"Результат: {path}")
    return 0


def _diff(item: tuple[float, float, float]) -> str:
    mean, low, high = item
    return f"{mean:+.3f} [{low:+.3f}; {high:+.3f}]"


async def cmd_compare(args: argparse.Namespace) -> int:
    questions = load_questions(QUESTIONS)
    runs = {Path(a).name: _read_json(Path(a)) for a in args.answers}
    table = compare_runs({name: outcomes(r["records"], questions) for name, r in runs.items()})
    lines = [
        "| Прогон | Подтверждено | С ответом | Равен лучшему | Медиана, с |",
        "|---|---|---|---|---|",
    ]
    for name, row in table.items():
        m = row["summary"]
        lines.append(
            f"| {name} | {m['supported_share']['mean']} {_diff(row['vs_best']['supported_share'])} "
            f"| {m['answered_share']['mean']} {_diff(row['vs_best']['answered_share'])} "
            f"| {'да' if row['equal_to_best'] else 'нет'} | {m['latency']['median_s']} |"
        )
    path = RESULTS / f"answers-compare-{datetime.now(UTC).date()}-{args.tag}.json"
    _write_json(path, {"date": datetime.now(UTC).isoformat(timespec="seconds"), "runs": table})
    print("\n".join(lines))  # noqa: T201
    _log(f"Результат: {path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    answer = sub.add_parser("answer")
    answer.add_argument("--database-url", required=True)
    answer.add_argument("--llm-url", required=True)
    answer.add_argument("--llm-model", default=MODEL_ALIAS)
    # По умолчанию — боевые поиск и ответ (ADR-0009).
    answer.add_argument("--retrieval", default="rerank=bge-v2-m3@20")
    answer.add_argument("--citation", choices=["quote", "sentences"], default=CITATION)
    answer.add_argument("--fragments", type=int, default=MAX_FRAGMENTS)
    answer.add_argument("--claims", type=int, default=MAX_CLAIMS)
    answer.add_argument("--split", choices=["dev", "test"], default="dev")
    answer.add_argument("--tag", required=True)
    answer.add_argument("--concurrency", type=int, default=1)
    answer.add_argument("--limit", type=int, default=None)
    judge = sub.add_parser("judge")
    judge.add_argument("--answers", required=True)
    agree = sub.add_parser("judge-agree")
    agree.add_argument("--answers", required=True, nargs="+")
    agree.add_argument("--count", type=int, default=60, help="сколько ответов судит вторая модель")
    agree.add_argument("--seed", type=int, default=70)
    compare = sub.add_parser("compare")
    compare.add_argument("--answers", required=True, nargs="+")
    compare.add_argument("--tag", required=True)
    for command in (judge, agree):
        command.add_argument("--llm-url", required=True)
        command.add_argument("--llm-model", default=MODEL_ALIAS)
        command.add_argument("--concurrency", type=int, default=1)
    args = parser.parse_args()
    handlers = {
        "answer": cmd_answer,
        "judge": cmd_judge,
        "judge-agree": cmd_judge_agree,
        "compare": cmd_compare,
    }
    handler = handlers[args.command]
    return asyncio.run(handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
