"""Подбор RAG на наборе v3 (спека #92, раздел 7).

    uv run --package clin-compass-eval python eval/rag/run_v3.py <команда> [флаги]

Команды:
- `rewrite --database-url --llm-url --mode one|multi` — запросы переписывания v2 и v3 в кэш;
- `questions --database-url --llm-url` — вопросы к фрагментам индекса в кэш (спека #105, 5.1);
- `embed --database-url --url` — векторы запросов, фрагментов и вопросов к фрагментам (giga-3b);
- `rerank --database-url --url` — баллы bge-reranker-v2-m3 для поисковых вариантов;
- `label --database-url --llm-url` — разметка объединённой выдачи v3 и v2, пишет наборы;
- `stage-a --database-url [--split dev]` — поисковые варианты на v3 и v2, этап A;
- `answer --database-url --llm-url --variant <имя> --set v3|v2 --split dev|test [--limit N]`;
- `judge --llm-url --answers <файл> … [--force]` — судьи утверждений, пунктов и `must_not`;
- `select --answers <файл> … --tag <метка>` — сравнение вариантов v3 по правилу 4.4;
- `control --v3 <победитель> <база> --v2 <победитель> <база>` — контроль;
- `sample --answers <файл> --out <файл вне репозитория>` — вердикты судьи пунктов медику.

Ответы идут по одному: так время этапов честное. Файлы ответов содержат цитаты КР и не
коммитятся (`answers-2*.json` в `.gitignore`); итоги этапов, выбора и контроля — без них.
Расчёт этапа A, таблицы и выборка — `v3_report.py`, окружение — `v3_world.py`.
Файл длиннее 400 строк: здесь одиннадцать команд одного прогона и их ввод-вывод, расчёты вынесены.
"""

import argparse
import asyncio
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from clin_compass.core.ai.llm import MODEL_ALIAS, Llm, build_llm_model
from clin_compass.core.ai.nodes import AiError
from clin_compass.core.ai.runs import NullRecorder, RunRecord, RunRecorder, describe_error
from clin_compass.core.config import Settings
from clin_compass.modules.knowledge import service
from clin_compass.modules.knowledge.answering import MAX_CLAIMS
from clin_compass.modules.knowledge.questions import Questioner
from clin_compass.modules.knowledge.rewriting import Rewriter

from cache import CACHE, JsonStore, VectorStore, text_key
from dataset import QUESTIONS, Question, check_index, dump_questions, fingerprint
from dataset_v3 import QUESTIONS_V3, QuestionV3, dump_questions_v3, evidence_keys
from judge import ClaimJudge, MustNotJudge, PointJudge, RelevanceJudge
from labeling import grade_pool
from rag_metrics import answer_diff, answer_summary
from retrieval import Doc
from run import in_order
from tune_io import (
    EMBEDDING_MODELS,
    embed_texts,
    load_docs,
    rerank_document,
    rerank_key,
    rerank_scores,
)
from v3_io import merge_grades, merge_v2_grades, none_with_answer, unjudged
from v3_metrics import breakdown, point_losses, select
from v3_questions import QUESTIONS_CACHE, cached_questions, questions_key
from v3_report import load_run, markdown_table, medic_sample, stage_a, stage_a_lines
from v3_search import RERANKER, Variant
from v3_world import (
    DEPTH,
    RETRIEVAL,
    REWRITES,
    Item,
    World,
    all_queries,
    build_world,
    cached_rewrite,
    load_items,
    question_version,
    rewrite_key,
    rewrite_versions,
)
from variants import document_text

RESULTS = Path(__file__).resolve().parent / "results"
POOL_DEPTH = 10


def _log(message: str) -> None:
    print(message, file=sys.stderr)  # noqa: T201


def _today() -> str:
    return str(datetime.now(UTC).date())


def _llm(args: argparse.Namespace, recorder: RunRecorder | None = None) -> Llm:
    settings = Settings(
        database_url=args.database_url or "postgresql+asyncpg://unused/none",
        llm_urls=(args.llm_url,),
        llm_timeout_seconds=600,
    )
    return Llm(build_llm_model(settings, model=MODEL_ALIAS), recorder or NullRecorder())


def _write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


async def _world(
    args: argparse.Namespace, *, strict: bool, with_questions: bool = True
) -> tuple[World, list[Item]]:
    items = load_items("both")
    queries = all_queries(items, JsonStore(REWRITES), rewrite_versions())
    world = await build_world(
        args.database_url, queries, strict=strict, with_questions=with_questions
    )
    return world, items


async def cmd_rewrite(args: argparse.Namespace) -> int:
    rewriter = Rewriter(_llm(args), mode=args.mode)
    store = JsonStore(REWRITES)

    def needed(item: Item) -> bool:
        key = rewrite_key(rewriter.prompt_version, item.question, item.patient)
        # Сбой узла не кэшируется навсегда: такие вопросы переписываются заново.
        return bool(store.missing([key])) or store.get(key)["error"] is not None

    todo = [i for i in load_items("both") if needed(i)]
    for n, item in enumerate(todo, start=1):
        started = time.perf_counter()
        error = None
        try:
            queries = await rewriter.rewrite(item.question, context=item.patient)
        except AiError as exc:
            queries, error = [], describe_error(exc)
        elapsed = round(time.perf_counter() - started, 2)
        key = rewrite_key(rewriter.prompt_version, item.question, item.patient)
        store.put({key: {"queries": queries, "latency_s": elapsed, "error": error}})
        _log(f"{args.mode} {n}/{len(todo)} {item.id}: {len(queries)} запр., {elapsed} с")
    _log(f"Переписывание {args.mode}: {rewriter.prompt_version}, новых {len(todo)}")
    return 0


async def cmd_questions(args: argparse.Namespace) -> int:
    docs = await load_docs(args.database_url)
    questioner = Questioner(_llm(args))
    store = JsonStore(QUESTIONS_CACHE)
    todo = [
        d for d in docs.values() if store.missing([questions_key(questioner.prompt_version, d)])
    ]

    async def one(doc: Doc) -> None:
        questions = await questioner.questions(doc.title, doc.heading_path, doc.text)
        store.put({questions_key(questioner.prompt_version, doc): questions})
        _log(f"вопросы: {doc.key} ({len(questions)})")

    await in_order(todo, one, concurrency=args.concurrency)
    _log(f"Вопросы к фрагментам: {questioner.prompt_version}, новых {len(todo)} из {len(docs)}")
    return 0


async def cmd_embed(args: argparse.Namespace) -> int:
    world, items = await _world(args, strict=False, with_questions=False)
    store = VectorStore(CACHE / "embeddings" / "giga-3b.npz")
    docs = [document_text(d.title, d.heading_path, d.text, None) for d in world.docs.values()]
    by_key = cached_questions(JsonStore(QUESTIONS_CACHE), question_version(), world.docs)
    docs += [q for questions in by_key.values() for q in questions]
    queries = all_queries(items, JsonStore(REWRITES), rewrite_versions())
    async with httpx.AsyncClient(base_url=args.url, timeout=600) as client:
        for role, texts, is_query in (("doc", docs, False), ("query", queries, True)):
            missing = [t for t in texts if store.missing([text_key(role, t)])]
            for start in range(0, len(missing), 64):
                chunk = missing[start : start + 64]
                vectors = await embed_texts(
                    client, EMBEDDING_MODELS["giga-3b"], chunk, query=is_query, batch=8
                )
                store.put({text_key(role, t): v for t, v in zip(chunk, vectors, strict=True)})
                _log(f"giga-3b {role}: {start + len(chunk)}/{len(missing)}")
    return 0


async def cmd_rerank(args: argparse.Namespace) -> int:
    world, items = await _world(args, strict=False)
    store, versions = JsonStore(REWRITES), rewrite_versions()
    for name in (*RETRIEVAL, *args.extra):
        variant = Variant.from_name(name)
        for item in items:
            world.retrieve(variant, item, cached_rewrite(store, versions, variant, item)[0])
    wanted = sorted({p for s in world.sources.values() for p in s.wanted.get(RERANKER, set())})
    scores = JsonStore(CACHE / "rerank" / f"{RERANKER}.json")
    by_query: dict[str, list[str]] = {}
    for query, key in wanted:
        by_query.setdefault(query, []).append(key)
    async with httpx.AsyncClient(base_url=args.url, timeout=600) as client:
        for n, (query, keys) in enumerate(by_query.items(), start=1):
            docs = [world.docs[k] for k in keys]
            found = await rerank_scores(client, query, [rerank_document(d) for d in docs])
            scores.put({rerank_key(query, d): s for d, s in zip(docs, found, strict=True)})
            _log(f"{RERANKER}: запрос {n}/{len(by_query)}, документов {len(keys)}")
    return 0


def _judge_text(item: Item) -> str:
    if not item.patient:
        return item.question
    return f"{item.question}\n\nСведения о пациенте: {item.patient}"


def _pool(world: World, store: JsonStore, versions: dict[str, str], item: Item) -> list[str]:
    """Объединённая выдача: первые `POOL_DEPTH` фрагментов каждого поискового варианта."""
    pool: dict[str, None] = {}
    for name in RETRIEVAL:
        variant = Variant.from_name(name)
        keys, _ = world.retrieve(variant, item, cached_rewrite(store, versions, variant, item)[0])
        pool.update(dict.fromkeys(keys[:POOL_DEPTH]))
    return list(pool)


def _header(path: Path) -> str:
    return "".join(line + "\n" for line in path.read_text().splitlines() if line.startswith("#"))


async def cmd_label(args: argparse.Namespace) -> int:
    world, items = await _world(args, strict=True)
    store, versions = JsonStore(REWRITES), rewrite_versions()
    judge = RelevanceJudge(_llm(args))
    labels = JsonStore(CACHE / "labels.json")

    async def grade(item: Item, text: str, known: set[str]) -> dict[str, int]:
        todo = [k for k in _pool(world, store, versions, item) if k not in known]
        grades = await grade_pool(judge, labels, world.engines[True], text, todo, MODEL_ALIAS)
        _log(f"разметка {item.id}: {len(todo)} фрагм.")
        return grades

    def prints(grades: dict[str, int]) -> dict[str, str]:
        return {k: fingerprint(world.docs[k].text) for k in grades}

    async def one_v3(item: Item) -> tuple[QuestionV3, list[str]]:
        assert item.v3 is not None
        grades = await grade(item, _judge_text(item), evidence_keys(item.v3))
        found = none_with_answer(item.v3.kind, grades)
        return merge_grades(item.v3, grades, prints(grades)), found

    # v2 (спека #105, 3.4): новые ОХЛП и фрагменты, которые поисковые варианты подняли впервые.
    async def one_v2(item: Item) -> tuple[Question, list[str]]:
        assert item.v2 is not None
        grades = await grade(item, item.question, {r.key for r in item.v2.relevant})
        found = none_with_answer(item.v2.kind, grades)
        return merge_v2_grades(item.v2, grades, prints(grades)), found

    v3 = await in_order(
        [i for i in items if i.v3 is not None], one_v3, concurrency=args.concurrency
    )
    dump_questions_v3([q for q, _ in v3], QUESTIONS_V3, header=_header(QUESTIONS_V3))
    v2 = await in_order(
        [i for i in items if i.v2 is not None], one_v2, concurrency=args.concurrency
    )
    dump_questions([q for q, _ in v2], QUESTIONS, header=_header(QUESTIONS))
    # Вопрос без ответа → фрагменты с оценкой 2: по ним медик проверяет вид вопроса.
    conflicts = {q.id: keys for q, keys in v3 if keys}
    conflicts_v2 = {q.id: keys for q, keys in v2 if keys}
    _write(
        RESULTS / f"v3-label-{_today()}.json",
        {
            "judge": judge.prompt_version,
            "pool_depth": POOL_DEPTH,
            "none_with_answer": conflicts,
            "v2_none_with_answer": conflicts_v2,
        },
    )
    _log(f"Вопросы без ответа, где судья нашёл ответ: v3 {conflicts}, v2 {conflicts_v2}")
    return 0


async def cmd_stage_a(args: argparse.Namespace) -> int:
    world, items = await _world(args, strict=True)
    result = stage_a(world, items, args.split, extra=args.extra)
    _write(RESULTS / f"v3-stage-a-{_today()}-{args.split}.json", result)
    print("\n".join(stage_a_lines(result)))  # noqa: T201
    return 0


class StageRecorder(NullRecorder):
    """Запоминает каждый вызов: задачу, время и токены."""

    def __init__(self) -> None:
        self.runs: list[RunRecord] = []

    async def record(self, run: RunRecord) -> Any:
        self.runs.append(run)
        return await super().record(run)


def _seconds(runs: list[RunRecord], task: str) -> float:
    return round(sum(r.latency_ms for r in runs if r.task == task) / 1000, 2)


async def _answer_one(
    ai: service.KnowledgeAi,
    world: World,
    variant: Variant,
    item: Item,
    recorder: StageRecorder,
    rewrite: tuple[list[str], float],
) -> dict[str, Any]:
    """Запись прогона ответа на один вопрос."""
    keys, scores = world.retrieve(variant, item, rewrite[0])
    before = len(recorder.runs)
    started = time.perf_counter()
    claims: list[dict[str, str]] = []
    dropped, error = 0, None
    try:
        answer = await ai.answer(item.question, world.hits(keys, scores), context=item.patient)
        claims = [
            {"key": c.fragment.key, "statement": c.statement, "quote": c.quote}
            for c in answer.claims
        ]
        dropped = answer.dropped
    except AiError as exc:
        error = describe_error(exc)
    elapsed = time.perf_counter() - started
    runs = recorder.runs[before:]
    return {
        "id": item.id,
        "found": keys[:DEPTH],
        "shown": world.shown(keys, scores),
        "claims": claims,
        "dropped": dropped,
        "latency_s": round(rewrite[1] + elapsed, 2),
        "stages": {
            "rewrite": rewrite[1],
            "answer": _seconds(runs, "knowledge.answer"),
            "verify": _seconds(runs, "knowledge.verify"),
        },
        "output_tokens": sum(r.output_tokens or 0 for r in runs if r.task == "knowledge.answer"),
        "error": error,
        "verdicts": None,
        "points": None,
        "must_not": None,
    }


async def cmd_answer(args: argparse.Namespace) -> int:
    variant = Variant.from_name(args.variant)
    world, items = await _world(args, strict=True)
    chosen = [i for i in items if (i.v3 if args.set == "v3" else i.v2) is not None]
    chosen = [i for i in chosen if i.split == args.split][: args.limit]
    index = {k: fingerprint(f.text) for k, f in world.fragments.items()}
    problems = check_index([i.v3 or i.v2 for i in chosen], index)  # type: ignore[misc]
    if problems:
        _log("Набор не совпадает с индексом:\n" + "\n".join(problems))
        return 1
    recorder = StageRecorder()
    ai = service.KnowledgeAi(
        None,  # type: ignore[arg-type]  # эмбеддинги не нужны: выдача из кэша подбора
        _llm(args, recorder),
        premise=variant.premise,
        verify=variant.verify,
        thinking=variant.thinking,
        aspects=variant.aspects,
        max_claims=variant.claims or MAX_CLAIMS,
    )
    store, versions = JsonStore(REWRITES), rewrite_versions()
    records = []
    for n, item in enumerate(chosen, start=1):
        rewrite = cached_rewrite(store, versions, variant, item)
        record = await _answer_one(ai, world, variant, item, recorder, rewrite)
        records.append(record)
        _log(
            f"{variant.name} {n}/{len(chosen)} {item.id}: {len(record['claims'])} утв., "
            f"{record['latency_s']} с {record['error'] or ''}"
        )
    result = {
        "date": datetime.now(UTC).isoformat(timespec="seconds"),
        "set": args.set,
        "split": args.split,
        "variant": variant.name,
        "versions": {"llm": MODEL_ALIAS, "prompt": ai.prompt_version},
        "errors": sum(r["error"] is not None for r in records),
        "records": records,
    }
    path = RESULTS / f"answers-{_today()}-v3x-{args.set}-{args.split}-{variant.name}.json"
    _write(path, result)
    _log(f"Результат: {path}; ошибок {result['errors']}")
    return 0


async def _judge_records(
    args: argparse.Namespace, data: dict[str, Any], items: dict[str, Item]
) -> dict[str, str]:
    llm = _llm(args)
    claims_judge, points_judge, must_not_judge = ClaimJudge(llm), PointJudge(llm), MustNotJudge(llm)

    async def one(record: dict[str, Any]) -> None:
        item = items[record["id"]]
        pairs = [(c["statement"], c["quote"]) for c in record["claims"]]
        if record["verdicts"] is None:
            record["verdicts"] = await claims_judge.verdicts(item.question, pairs)
        if item.v3 is not None and record["points"] is None:
            texts = [p.text for p in item.v3.points]
            record["points"] = await points_judge.covered(item.question, item.patient, pairs, texts)
            record["must_not"] = await must_not_judge.present(
                item.question, item.patient, pairs, item.v3.must_not
            )
        _log(f"судья: {record['id']}")

    await in_order(data["records"], one, concurrency=args.concurrency)
    return {
        "claims": claims_judge.prompt_version,
        "points": points_judge.prompt_version,
        "must_not": must_not_judge.prompt_version,
    }


def cmd_judge(args: argparse.Namespace) -> int:
    for name in args.answers:
        path = Path(name)
        data = json.loads(path.read_text(encoding="utf-8"))
        if args.force:
            data["records"] = [unjudged(r) for r in data["records"]]
        items = {i.id: i for i in load_items(data["set"])}
        data["judges"] = asyncio.run(_judge_records(args, data, items))
        _write(path, data)
    return 0


def cmd_select(args: argparse.Namespace) -> int:
    runs = {}
    for name in args.answers:
        data, outs = load_run(Path(name))
        runs[data["variant"]] = outs
    result = select(runs)
    for name, outs in runs.items():
        result["runs"][name]["by_type"] = breakdown(outs, "type")
        result["runs"][name]["by_style"] = breakdown(outs, "style")
        result["runs"][name]["point_losses"] = point_losses(outs)
    _write(RESULTS / f"v3-select-{_today()}-{args.tag}.json", result)
    print(markdown_table(result))  # noqa: T201
    for name, row in result["runs"].items():
        print(f"{name}: {row['point_losses'].get('all')}")  # noqa: T201
    print(f"Лидер: {result['leader']}; победитель: {result['winner']}")  # noqa: T201
    return 0


def cmd_control(args: argparse.Namespace) -> int:
    v3 = {}
    for name in args.v3:
        data, outs = load_run(Path(name))
        v3[data["variant"]] = outs
    result: dict[str, Any] = {"v3": select(v3)}
    for name, outs in v3.items():
        result["v3"]["runs"][name]["point_losses"] = point_losses(outs)
        result["v3"]["runs"][name]["by_type"] = breakdown(outs, "type")
    (_, winner_v2), (_, base_v2) = (load_run(Path(n)) for n in args.v2)
    diff = answer_diff(winner_v2, base_v2)
    result["v2"] = {
        "winner": answer_summary(winner_v2),
        "base": answer_summary(base_v2),
        "diff": diff,
        "regression": any(high < 0 for _, _, high in diff.values()),
    }
    _write(RESULTS / f"v3-control-{_today()}.json", result)
    print(markdown_table(result["v3"]))  # noqa: T201
    print(json.dumps(diff, ensure_ascii=False))  # noqa: T201
    print(f"Победитель: {result['v3']['winner']}; регрессия v2: {result['v2']['regression']}")  # noqa: T201
    return 0


def cmd_sample(args: argparse.Namespace) -> int:
    data = json.loads(Path(args.answers[0]).read_text(encoding="utf-8"))
    Path(args.out).write_text(medic_sample(data, args.count), encoding="utf-8")
    _log(f"Выборка: {args.out}")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    names = ("rewrite", "questions", "embed", "rerank", "label", "stage-a", "answer")
    for name in (*names, "judge", "select", "control", "sample"):
        command = sub.add_parser(name)
        if name in names:
            command.add_argument("--database-url", required=True)
        else:
            command.add_argument("--database-url", default="")
        if name in ("rewrite", "questions", "label", "answer", "judge"):
            command.add_argument("--llm-url", required=True)
        if name in ("embed", "rerank"):
            command.add_argument("--url", required=True)
    commands = sub.choices
    commands["rewrite"].add_argument("--mode", choices=["one", "multi"], required=True)
    commands["stage-a"].add_argument("--split", choices=["dev", "test"], default="dev")
    for name in ("rerank", "stage-a"):
        commands[name].add_argument("--extra", nargs="*", default=[], help="сочетания факторов")
    commands["answer"].add_argument("--variant", required=True)
    commands["answer"].add_argument("--set", choices=["v3", "v2"], default="v3")
    commands["answer"].add_argument("--split", choices=["dev", "test"], default="dev")
    commands["answer"].add_argument("--limit", type=int, default=None)
    for name in ("judge", "select", "sample"):
        commands[name].add_argument("--answers", nargs="+", required=True)
    commands["judge"].add_argument("--concurrency", type=int, default=2)
    commands["judge"].add_argument(
        "--force", action="store_true", help="оценить заново все записи: эталон изменился"
    )
    commands["questions"].add_argument("--concurrency", type=int, default=2)
    commands["label"].add_argument("--concurrency", type=int, default=2)
    commands["select"].add_argument("--tag", required=True)
    commands["control"].add_argument("--v3", nargs=2, required=True)
    commands["control"].add_argument("--v2", nargs=2, required=True)
    commands["sample"].add_argument("--count", type=int, default=40)
    commands["sample"].add_argument("--out", required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    asynchronous = {
        "rewrite": cmd_rewrite,
        "questions": cmd_questions,
        "embed": cmd_embed,
        "rerank": cmd_rerank,
        "label": cmd_label,
        "stage-a": cmd_stage_a,
        "answer": cmd_answer,
    }
    if args.command in asynchronous:
        return asyncio.run(asynchronous[args.command](args))
    synchronous = {
        "judge": cmd_judge,
        "select": cmd_select,
        "control": cmd_control,
        "sample": cmd_sample,
    }
    return synchronous[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
