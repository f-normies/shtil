"""Подбор поиска RAG (спека #70, раздел 5): кэш, разметка набора и сетка конфигураций.

    uv run --package clin-compass-eval python eval/rag/tune.py <команда> --database-url … [флаги]

Команды:
- `contexts --llm-url` — строка контекста к каждому фрагменту;
- `embed --model <имя> --url` — векторы фрагментов, частей и вопросов для всех вариантов текста;
- `rerank --model <имя> --url --stage pool|A|B [--draft]` — баллы ранжировщика для кандидатов
  этапа; `--draft` берёт вопросы черновика, чтобы посчитать их до разметки;
- `label --llm-url [--llm-model] [--limit N]` — разметка черновика судьёй и деление: пишет
  `questions.yaml`; с `--limit` только кладёт оценки первых N вопросов в кэш;
- `agree --model-b <имя> --limit N` — согласие двух судей на первых N вопросах;
- `grid --stage A|B --split dev|test [--configs a,b]` — метрики конфигураций этапа или только
  перечисленных: `results/tune-<дата>-<этап>.json`;
- `check --embeddings-url` — сверка базовой конфигурации с боевым `service.search`.

Факторы и порядок этапов — `stages.py`, сборка выдачи — `retrieval.py`, ввод-вывод — `tune_io.py`.
"""

import argparse
import asyncio
import json
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import yaml
from clin_compass.core.ai.embeddings import build_embedder
from clin_compass.core.ai.llm import MODEL_ALIAS, Llm, build_llm_model
from clin_compass.core.ai.runs import NullRecorder
from clin_compass.core.config import Settings
from clin_compass.core.db import make_engine, make_sessionmaker
from clin_compass.modules.knowledge import service
from clin_compass_knowledge.loader import load_yaml

from cache import CACHE, JsonStore, VectorStore, text_key
from contexts import Contextualizer, section_headings
from dataset import QUESTIONS, load_questions
from judge import RelevanceJudge
from labeling import agreement, label_cache_key, label_draft, pool_for
from rag_metrics import paired_interval, retrieval_scores, retrieval_summary
from retrieval import Config, Engine
from stages import passed_factors, stage_a_configs, stage_b_configs
from tune_io import (
    EMBEDDING_MODELS,
    CacheSources,
    embed_texts,
    full_text_rankings,
    load_docs,
    rerank_document,
    rerank_key,
    rerank_scores,
    vector_texts,
)
from variants import Abbreviations, parse_abbreviations

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
RESULTS = HERE / "results"
DRAFT = HERE / "questions.draft.yaml"
TERMS = HERE / "search_terms.yaml"
FIXES = HERE / "abbreviations.yaml"
MANIFEST = ROOT / "knowledge" / "sources" / "manifest.yaml"
GUIDELINES_API = "https://apicr.minzdrav.gov.ru/api.ashx"
CONCURRENCY = 2


def _log(message: str) -> None:
    print(message, file=sys.stderr)  # noqa: T201


async def load_abbreviations() -> Abbreviations:
    """Словарь из «Списка сокращений» КР манифеста (ответы API кэшируются) и исправлений."""
    manifest = load_yaml(MANIFEST.read_text(encoding="utf-8"))
    versions = [s["version"] for s in manifest["sources"] if s["kind"] == "clinical_guideline"]
    parsed = []
    async with httpx.AsyncClient(timeout=120) as client:
        for version in versions:
            path = CACHE / "guidelines" / f"{version}.json"
            if not path.exists():
                response = await client.get(
                    GUIDELINES_API, params={"op": "GetClinrec2", "id": version}
                )
                response.raise_for_status()
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(response.content)
            data = json.loads(path.read_text(encoding="utf-8"))
            sections = data.get("obj", data)["sections"]
            html = next(s["content"] for s in sections if s.get("id") == "doc_abbreviation")
            parsed.append(parse_abbreviations(html))
    fixes = {item["short"]: item["full"] for item in load_yaml(FIXES.read_text(encoding="utf-8"))}
    return Abbreviations.build(parsed, fixes)


def question_texts(abbreviations: Abbreviations) -> list[str]:
    """Вопросы черновика и набора: как есть и с раскрытыми сокращениями для обоих поисков."""
    texts: list[str] = []
    sources = []
    if DRAFT.exists():
        sources.extend(e["question"] for e in yaml.safe_load(DRAFT.read_text(encoding="utf-8")))
    sources.extend(q.question for q in load_questions(QUESTIONS))
    for question in dict.fromkeys(sources):
        texts += [
            question,
            abbreviations.for_embedding(question),
            abbreviations.for_full_text(question),
        ]
    return list(dict.fromkeys(texts))


def _contexts(docs: dict[str, Any]) -> dict[str, str]:
    store = JsonStore(CACHE / "contexts.json")
    found = {}
    for key, doc in docs.items():
        cache_key = text_key("context", doc.text)
        if not store.missing([cache_key]):
            found[key] = store.get(cache_key)
    return found


async def cmd_contexts(args: argparse.Namespace) -> int:
    docs = await load_docs(args.database_url)
    store = JsonStore(CACHE / "contexts.json")
    settings = Settings(database_url=args.database_url, llm_urls=(args.llm_url,))
    contextualizer = Contextualizer(Llm(build_llm_model(settings), NullRecorder()))
    headings = section_headings(docs)
    todo = [d for d in docs.values() if store.missing([text_key("context", d.text)])]
    gate = asyncio.Semaphore(CONCURRENCY)

    async def one(doc: Any) -> None:
        async with gate:
            line = await contextualizer.context(doc, headings[doc.source_id])
        store.put({text_key("context", doc.text): line})
        _log(f"контекст: {doc.key}")

    await asyncio.gather(*(one(d) for d in todo))
    done = len(docs) - len(todo)
    _log(f"Контекстов: {done} было, {len(todo)} новых, {contextualizer.prompt_version}")
    return 0


async def cmd_embed(args: argparse.Namespace) -> int:
    docs = await load_docs(args.database_url)
    contexts = _contexts(docs)
    model = EMBEDDING_MODELS[args.model]
    store = VectorStore(CACHE / "embeddings" / f"{args.model}.npz")
    doc_texts = {
        t
        for parts in (False, True)
        for context in (False, True)
        for pieces in vector_texts(docs, contexts=contexts, parts=parts, context=context).values()
        for t in pieces
    }
    queries = question_texts(await load_abbreviations())
    async with httpx.AsyncClient(base_url=args.url, timeout=600) as client:
        for role, texts, is_query in (("doc", sorted(doc_texts), False), ("query", queries, True)):
            missing = [t for t in texts if store.missing([text_key(role, t)])]
            for start in range(0, len(missing), 64):
                chunk = missing[start : start + 64]
                vectors = await embed_texts(client, model, chunk, query=is_query, batch=args.batch)
                store.put({text_key(role, t): v for t, v in zip(chunk, vectors, strict=True)})
                _log(f"{args.model} {role}: {start + len(chunk)}/{len(missing)}")
    return 0


async def _engine(args: argparse.Namespace, *, strict: bool) -> tuple[Engine, CacheSources]:
    docs = await load_docs(args.database_url)
    contexts = _contexts(docs)
    abbreviations = await load_abbreviations()
    full_text = await full_text_rankings(
        args.database_url, docs, contexts, question_texts(abbreviations)
    )
    sources = CacheSources(
        docs=docs, cache_dir=CACHE, contexts=contexts, full_text_rankings=full_text, strict=strict
    )
    terms = yaml.safe_load(TERMS.read_text(encoding="utf-8"))
    return Engine(docs=docs, sources=sources, abbreviations=abbreviations, terms=terms), sources


def _stage_configs(stage: str) -> list[Config]:
    if stage == "pool":
        return [Config(reranker="bge-v2-m3", rerank_depth=40)]
    if stage == "A":
        return stage_a_configs()
    passed = json.loads((RESULTS / "tune-passed.json").read_text(encoding="utf-8"))
    return stage_b_configs({factor: Config(**fields) for factor, fields in passed.items()})


def _questions_for(stage: str) -> list[str]:
    if stage == "pool":
        return [e["question"] for e in yaml.safe_load(DRAFT.read_text(encoding="utf-8"))]
    return [q.question for q in load_questions(QUESTIONS)]


async def cmd_rerank(args: argparse.Namespace) -> int:
    engine, sources = await _engine(args, strict=False)
    configs = [c for c in _stage_configs(args.stage) if c.reranker == args.model]
    # --draft: вопросы черновика — надмножество набора; так оценки считаются до разметки.
    questions = _questions_for("pool" if args.draft else args.stage)
    for config in configs:
        for question in questions:
            engine.rank(config, question)
    wanted = sorted(sources.wanted.get(args.model, set()))
    store = JsonStore(CACHE / "rerank" / f"{args.model}.json")
    by_query: dict[str, list[str]] = {}
    for query, key in wanted:
        by_query.setdefault(query, []).append(key)
    async with httpx.AsyncClient(base_url=args.url, timeout=600) as client:
        for n, (query, keys) in enumerate(by_query.items(), start=1):
            docs = [engine.docs[k] for k in keys]
            scores = await rerank_scores(client, query, [rerank_document(d) for d in docs])
            store.put({rerank_key(query, d): s for d, s in zip(docs, scores, strict=True)})
            _log(f"{args.model}: запрос {n}/{len(by_query)}, документов {len(keys)}")
    return 0


async def cmd_label(args: argparse.Namespace) -> int:
    engine, _ = await _engine(args, strict=True)
    # Длинная порция судьи на занятом узле идёт дольше боевых 180 с.
    settings = Settings(
        database_url=args.database_url, llm_urls=(args.llm_url,), llm_timeout_seconds=600
    )
    llm = Llm(build_llm_model(settings, model=args.llm_model), NullRecorder())
    draft = yaml.safe_load(DRAFT.read_text(encoding="utf-8"))
    return await label_draft(
        draft[: args.limit],
        engine,
        llm,
        out=QUESTIONS,
        review=CACHE / "label_review.yaml",
        model=args.llm_model,
        write=args.limit is None,
    )


async def cmd_agree(args: argparse.Namespace) -> int:
    """Согласие двух судей на первых `--limit` вопросах черновика: оценки берутся из кэша."""
    engine, _ = await _engine(args, strict=True)
    settings = Settings(database_url=args.database_url, llm_urls=("http://127.0.0.1:9",))
    version = RelevanceJudge(Llm(build_llm_model(settings), NullRecorder())).prompt_version
    store = JsonStore(CACHE / "labels.json")
    draft = yaml.safe_load(DRAFT.read_text(encoding="utf-8"))[: args.limit]
    pairs = []
    for entry in draft:
        origin = [o["key"] for o in entry["origin"]]
        for key in pool_for(engine, entry["question"], origin):
            text = engine.docs[key].text
            a, b = (
                label_cache_key(m, version, entry["question"], text)
                for m in (args.model_a, args.model_b)
            )
            if not store.missing([a, b]):
                pairs.append((int(store.get(a)), int(store.get(b))))
    print(json.dumps(agreement(pairs), ensure_ascii=False))  # noqa: T201
    confusion = Counter(pairs)
    for a in (0, 1, 2):
        row = "  ".join(f"{args.model_b}={b}: {confusion[(a, b)]}" for b in (0, 1, 2))
        print(f"{args.model_a}={a}: {row}")  # noqa: T201
    return 0


def _diff(per: dict[str, list[dict[str, float]]], name: str, ref: str) -> dict[str, list[float]]:
    return {
        metric: list(paired_interval([s[metric] for s in per[name]], [s[metric] for s in per[ref]]))
        for metric in ("recall_at_5", "recall_at_10", "mrr", "ndcg_at_10")
    }


async def cmd_grid(args: argparse.Namespace) -> int:
    engine, _ = await _engine(args, strict=True)
    questions = [q for q in load_questions(QUESTIONS) if q.split == args.split and q.kind != "none"]
    # --configs: только эти конфигурации; так контрольная часть считается один раз
    # для итоговой конфигурации и базы (спека #70, 5.4).
    configs = (
        [Config.from_name(name) for name in args.configs.split(",")]
        if args.configs
        else _stage_configs(args.stage)
    )
    per: dict[str, list[dict[str, float]]] = {}
    found: dict[str, dict[str, list[str]]] = {}
    for config in configs:
        rankings = {q.id: engine.rank(config, q.question) for q in questions}
        per[config.name] = [retrieval_scores(q, rankings[q.id]) for q in questions]
        found[config.name] = {qid: keys[:10] for qid, keys in rankings.items()}
    result: dict[str, Any] = {
        "date": datetime.now(UTC).isoformat(timespec="seconds"),
        "stage": args.stage,
        "split": args.split,
        "questions": len(questions),
        "configs": {
            name: {"summary": retrieval_summary(scores), "diff_vs_base": _diff(per, name, "base")}
            for name, scores in per.items()
        },
        "found_top10": found,
    }
    if args.stage == "A" and not args.configs:
        passed = passed_factors(
            {
                n: {m: tuple(v) for m, v in c["diff_vs_base"].items()}
                for n, c in result["configs"].items()
            }
        )
        result["passed"] = {f: c.name for f, c in passed.items()}
        (RESULTS / "tune-passed.json").write_text(
            json.dumps({f: c.__dict__ for f, c in passed.items()}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    RESULTS.mkdir(exist_ok=True)
    path = RESULTS / f"tune-{datetime.now(UTC).date()}-{args.stage}-{args.split}.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _log(f"Результат: {path}")
    for name, item in sorted(
        result["configs"].items(), key=lambda kv: -kv[1]["summary"]["recall_at_5"]["mean"]
    ):
        s, d = item["summary"], item["diff_vs_base"]["recall_at_5"]
        print(  # noqa: T201
            f"{name:40} r@5 {s['recall_at_5']['mean']:.3f} r@10 {s['recall_at_10']['mean']:.3f} "
            f"mrr {s['mrr']['mean']:.3f} ndcg {s['ndcg_at_10']['mean']:.3f} "
            f"Δr@5 {d[0]:+.3f} [{d[1]:+.3f}; {d[2]:+.3f}]"
        )
    return 0


async def cmd_check(args: argparse.Namespace) -> int:
    """Первая десятка базовой конфигурации против боевого `service.search` на тех же вопросах."""
    engine, _ = await _engine(args, strict=True)
    settings = Settings(database_url=args.database_url, embeddings_urls=(args.embeddings_url,))
    embedder = build_embedder(settings, NullRecorder(), task="knowledge.search")
    db = make_engine(args.database_url)
    same = 0
    questions = _questions_for("pool")
    try:
        maker = make_sessionmaker(db)
        for question in questions:
            async with maker() as session:
                hits = await service.search(session, embedder, question, k=10)
            if [h.fragment.key for h in hits] == engine.rank(Config(), question)[:10]:
                same += 1
    finally:
        await db.dispose()
    share = same / len(questions)
    _log(f"Совпадение первой десятки с боевым поиском: {same}/{len(questions)} ({share:.1%})")
    return 0 if share >= 0.95 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("contexts", "embed", "rerank", "label", "agree", "grid", "check"):
        command = sub.add_parser(name)
        command.add_argument("--database-url", required=True)
        if name in ("contexts", "label"):
            command.add_argument("--llm-url", required=True)
        if name == "label":
            command.add_argument("--llm-model", default=MODEL_ALIAS)
            command.add_argument("--limit", type=int, default=None)
        if name == "agree":
            command.add_argument("--model-a", default=MODEL_ALIAS)
            command.add_argument("--model-b", required=True)
            command.add_argument("--limit", type=int, required=True)
        if name in ("embed", "rerank"):
            command.add_argument("--model", required=True)
            command.add_argument("--url", required=True)
        if name == "embed":
            command.add_argument("--batch", type=int, default=8)
        if name == "rerank":
            command.add_argument("--stage", choices=["pool", "A", "B"], required=True)
            command.add_argument("--draft", action="store_true")
        if name == "grid":
            command.add_argument("--stage", choices=["A", "B"], required=True)
            command.add_argument("--split", choices=["dev", "test"], default="dev")
            command.add_argument("--configs", default=None, help="имена через запятую")
        if name == "check":
            command.add_argument("--embeddings-url", required=True)
    args = parser.parse_args()
    handlers = {
        "contexts": cmd_contexts,
        "embed": cmd_embed,
        "rerank": cmd_rerank,
        "label": cmd_label,
        "agree": cmd_agree,
        "grid": cmd_grid,
        "check": cmd_check,
    }
    return asyncio.run(handlers[args.command](args))


if __name__ == "__main__":
    raise SystemExit(main())
