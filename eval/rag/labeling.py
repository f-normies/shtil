"""Разметка черновика судьёй и деление набора (спека #70, 3.2–3.3).

Для каждого вопроса — объединённая выдача: первые 10 из шести поисков (вектор Giga-3B,
Qwen3-Embedding-4B, bge-m3, полнотекстовый, гибридный, bge-reranker по 40 лучшим
гибридного) плюс исходные фрагменты. Судья оценивает её порциями. Оценки кэшируются
по вопросу и фрагменту. Вопросы, не прошедшие правила `keep_after_labeling`, выбрасываются
и попадают в файл проверки вместе со случайными 10% оценок.
"""

import asyncio
import random
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import yaml
from clin_compass.core.ai.llm import MODEL_ALIAS, Llm
from clin_compass.core.ai.nodes import AiNoAnswerError, AiOutputError

from cache import CACHE, JsonStore, text_key
from dataset import (
    Question,
    Relevant,
    dump_questions,
    fingerprint,
    keep_after_labeling,
    split_ids,
)
from judge import Passage, RelevanceJudge, batches
from retrieval import Config, Engine
from stages import pool_keys

POOL_DEPTH = 10
POOL_EMBEDDINGS = ("giga-3b", "qwen3-emb-4b", "bge-m3")
BATCH_COUNT = 30
# Порция до 16 тыс. знаков (~5 тыс. токенов): два параллельных запроса не забивают KV-кэш LLM.
BATCH_CHARS = 16_000
REVIEW_SHARE = 0.1
TEST_SHARE = 2 / 7
SEED = 70
# Один запрос за раз: два длинных входа параллельно не помещаются в память Мака 36 ГБ
# вместе с весами LLM, и разбор входа падает втрое (сжатие памяти).
CONCURRENCY = 1


def _log(message: str) -> None:
    print(message, file=sys.stderr)  # noqa: T201


# Первые оценки кэша писала Qwen3.8-27B, тогда ключ был без имени модели. Боевая LLM с тех пор
# сменилась (ADR-0009), поэтому прежний формат привязан к имени, а не к `MODEL_ALIAS`.
_UNNAMED_KEY_MODEL = "qwen3.8-27b"


def label_cache_key(model: str, prompt_version: str, question: str, text: str) -> str:
    """Ключ оценки в кэше. У Qwen3.8 — прежний формат без модели: её оценки сохраняются."""
    head = prompt_version if model == _UNNAMED_KEY_MODEL else f"{model}\n{prompt_version}"
    return text_key("label", f"{head}\n{question}\n{text}")


def agreement(pairs: Sequence[tuple[int, int]]) -> dict[str, Any]:
    """Согласие двух судей по парам оценок одного фрагмента для одного вопроса.

    `exact` — та же оценка; `relevant` — согласие в «оценка ≥ 1»; `answers` — в «оценка 2».
    """
    if not pairs:
        return {"pairs": 0, "exact": None, "relevant": None, "answers": None}
    n = len(pairs)
    return {
        "pairs": n,
        "exact": round(sum(a == b for a, b in pairs) / n, 3),
        "relevant": round(sum((a >= 1) == (b >= 1) for a, b in pairs) / n, 3),
        "answers": round(sum((a == 2) == (b == 2) for a, b in pairs) / n, 3),
    }


def pool_for(engine: Engine, question: str, origin: Sequence[str]) -> list[str]:
    """Объединённая выдача шести поисков для вопроса."""
    rankings = [engine.sources.vector(Config(embeddings=m), question) for m in POOL_EMBEDDINGS]
    rankings.append(engine.sources.full_text(Config(), question))
    rankings.append(engine.rank(Config(), question))
    rankings.append(engine.rank(Config(reranker="bge-v2-m3", rerank_depth=40), question))
    return pool_keys(rankings, origin=origin, depth=POOL_DEPTH)


async def grade_pool(
    judge: RelevanceJudge,
    store: JsonStore,
    engine: Engine,
    question: str,
    pool: list[str],
    model: str,
) -> dict[str, int]:
    """Оценки судьи для фрагментов `pool`; оценки из кэша не запрашиваются повторно."""

    def cache_key(key: str) -> str:
        return label_cache_key(model, judge.prompt_version, question, engine.docs[key].text)

    grades = {k: int(store.get(cache_key(k))) for k in pool if not store.missing([cache_key(k)])}
    todo = [
        Passage(key=k, title=d.title, heading_path=d.heading_path, text=d.text)
        for k in pool
        if k not in grades
        for d in [engine.docs[k]]
    ]
    for portion in batches(todo, max_count=BATCH_COUNT, max_chars=BATCH_CHARS):
        found = await judge.grade(question, portion)
        store.put({cache_key(k): g for k, g in found.items()})
        grades.update(found)
    return grades


def _write_set(questions: Sequence[Question], out: Path) -> None:
    """Пишет набор, сохраняя шапку-комментарий файла."""
    lines = out.read_text(encoding="utf-8").splitlines()
    header = "".join(line + "\n" for line in lines if line.startswith("#"))
    dump_questions(questions, out, header=header)


def _write_review(review: Path, content: dict[str, Any]) -> None:
    review.parent.mkdir(parents=True, exist_ok=True)
    review.write_text(
        yaml.safe_dump(content, allow_unicode=True, sort_keys=False, width=100), encoding="utf-8"
    )


def _group(engine: Engine, entry: dict[str, Any]) -> str:
    origin = entry["origin"]
    source = engine.docs[origin[0]["key"]].source_id if origin else "none"
    return f"{source}/{entry['kind']}"


async def label_draft(
    draft: Sequence[dict[str, Any]],
    engine: Engine,
    llm: Llm,
    *,
    out: Path,
    review: Path,
    model: str = MODEL_ALIAS,
    write: bool = True,
) -> int:
    """Размечает черновик, делит набор и пишет `out` и файл проверки `review`.

    `write=False` — только оценки в кэш: так второй судья размечает часть черновика
    для сравнения (`tune.py agree`).
    """
    judge = RelevanceJudge(llm)
    store = JsonStore(CACHE / "labels.json")
    index = {key: fingerprint(doc.text) for key, doc in engine.docs.items()}
    stale = [
        e["id"] for e in draft if any(index.get(o["key"]) != o["fingerprint"] for o in e["origin"])
    ]
    if stale:
        _log(f"Исходные фрагменты изменились после черновика: {', '.join(stale)}")
        return 1
    gate = asyncio.Semaphore(CONCURRENCY)

    async def one(entry: dict[str, Any]) -> dict[str, int] | None:
        origin = [o["key"] for o in entry["origin"]]
        pool = pool_for(engine, entry["question"], origin)
        async with gate:
            try:
                grades = await grade_pool(judge, store, engine, entry["question"], pool, model)
            except (AiOutputError, AiNoAnswerError) as exc:
                # Оценки готовых порций уже в кэше: повторный запуск продолжит с этого места.
                _log(f"разметка: {entry['id']} не размечен: {type(exc).__name__}")
                return None
        _log(f"разметка: {entry['id']} ({len(pool)} фрагментов)")
        return grades

    results = await asyncio.gather(*(one(e) for e in draft))
    failed = [e["id"] for e, g in zip(draft, results, strict=True) if g is None]
    if failed:
        _log(
            f"Не размечены: {', '.join(failed)}. Запустите label ещё раз: готовое берётся из кэша."
        )
        return 1
    all_grades = [g for g in results if g is not None]
    if not write:
        _log(f"Оценки {model} для {len(draft)} вопросов — в кэше.")
        return 0
    kept: list[tuple[dict[str, Any], dict[str, int]]] = []
    dropped: list[dict[str, Any]] = []
    for entry, grades in zip(draft, all_grades, strict=True):
        origin = [o["key"] for o in entry["origin"]]
        if keep_after_labeling(entry["kind"], origin, grades):
            kept.append((entry, grades))
        else:
            dropped.append(
                {
                    "id": entry["id"],
                    "kind": entry["kind"],
                    "question": entry["question"],
                    "origin_grades": {k: grades.get(k, 0) for k in origin},
                    "best_other": sorted(
                        ((g, k) for k, g in grades.items() if k not in origin), reverse=True
                    )[:3],
                    "source_text": entry.get("source_text"),
                }
            )
    ids = {entry["id"]: f"q-{n:03d}" for n, (entry, _) in enumerate(kept, start=1)}
    splits = split_ids(
        [(ids[e["id"]], _group(engine, e)) for e, _ in kept], test_share=TEST_SHARE, seed=SEED
    )
    questions = [
        Question(
            id=ids[entry["id"]],
            question=entry["question"],
            kind=entry["kind"],
            split=splits[ids[entry["id"]]],
            origin=[o["key"] for o in entry["origin"]],
            relevant=[
                Relevant(key=k, fingerprint=index[k], grade=g)  # type: ignore[arg-type]
                for k, g in sorted(grades.items(), key=lambda kv: -kv[1])
                if g >= 1
            ],
        )
        for entry, grades in kept
    ]
    _write_set(questions, out)

    judgments = [
        {"question": e["question"], "key": k, "grade": g, "text": engine.docs[k].text}
        for e, grades in zip(draft, all_grades, strict=True)
        for k, g in grades.items()
    ]
    sample = random.Random(SEED).sample(judgments, round(len(judgments) * REVIEW_SHARE))
    _write_review(review, {"sample": sample, "dropped": dropped})
    kinds = Counter(q.kind for q in questions)
    split_counts = Counter(q.split for q in questions)
    grade_counts = Counter(j["grade"] for j in judgments)
    _log(
        f"Набор: {len(questions)} вопросов {dict(kinds)}, деление {dict(split_counts)}; "
        f"выброшено {len(dropped)} {dict(Counter(d['kind'] for d in dropped))}; "
        f"оценок {len(judgments)} {dict(grade_counts)}; проверка: {review}"
    )
    return 0
