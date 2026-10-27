"""Сверяет llama-server с эталоном sentence-transformers через клиент core/ai (спека #16, раздел 5).

    uv run --package clin-compass-eval python eval/embeddings/check.py --base-url http://<адрес>:8001

Падает, если косинус любого вектора к эталону ниже порога или top-1 вопроса разошёлся с эталоном.
Совпадение top-1 с разметкой корпуса — справка о качестве модели, на результат не влияет.
"""

import argparse
import asyncio
import logging
import sys

import numpy as np

from common import (
    Matrix,
    configure_logging,
    corpus_sha256,
    embedder_for,
    load_corpus,
    load_reference,
    normalize,
    read_versions,
)

MIN_COSINE = 0.999
# /v1/embeddings отдаёт L2-нормализованные векторы; от этого зависит поиск по скалярному
# произведению. Сравнение с эталоном нормализует сам, поэтому нормы проверяем отдельно.
MAX_NORM_DEVIATION = 1e-3

log = logging.getLogger(__name__)


def _rowwise_cosine(actual: Matrix, expected: Matrix) -> Matrix:
    return np.sum(normalize(actual) * normalize(expected), axis=1)


def _top1(queries: Matrix, fragments: Matrix) -> list[int]:
    return [int(i) for i in np.argmax(normalize(queries) @ normalize(fragments).T, axis=1)]


async def run(base_url: str) -> bool:
    """Считает векторы корпуса на сервере и пишет отчёт в лог. True — проверка пройдена."""
    corpus = load_corpus()
    reference = load_reference()
    if reference.meta["corpus_sha256"] != corpus_sha256():
        log.error("Корпус изменился после расчёта эталона: перезапустите make_reference.py.")
        return False
    if reference.meta["model_revision"] != read_versions()["MODEL_REVISION"]:
        log.error("Эталон посчитан для другой ревизии модели: перезапустите make_reference.py.")
        return False

    embedder = embedder_for(base_url)
    query_result = await embedder.embed_query(corpus.queries)
    fragment_result = await embedder.embed_documents(corpus.fragments)
    queries = np.asarray(query_result.embeddings, np.float32)
    fragments = np.asarray(fragment_result.embeddings, np.float32)

    query_cos = _rowwise_cosine(queries, reference.queries)
    fragment_cos = _rowwise_cosine(fragments, reference.fragments)
    norm_deviation = np.abs(np.linalg.norm(np.concatenate([queries, fragments]), axis=1) - 1)
    server_top1 = _top1(queries, fragments)
    reference_top1 = _top1(reference.queries, reference.fragments)
    expected = [corpus.fragment_ids.index(fragment_id) for fragment_id in corpus.expected_ids]

    top1_same = sum(s == r for s, r in zip(server_top1, reference_top1, strict=True))
    server_hits = sum(s == e for s, e in zip(server_top1, expected, strict=True))
    reference_hits = sum(r == e for r, e in zip(reference_top1, expected, strict=True))
    total = len(corpus.queries)
    meta = reference.meta

    log.info(
        "Эталон: %s@%s, sentence-transformers %s, %s",
        meta["model_repo"],
        meta["model_revision"][:7],
        meta["sentence_transformers"],
        meta["dtype"],
    )
    log.info("Косинус, вопросы:   мин %.5f, среднее %.5f", query_cos.min(), query_cos.mean())
    log.info("Косинус, фрагменты: мин %.5f, среднее %.5f", fragment_cos.min(), fragment_cos.mean())
    log.info("Отклонение нормы векторов сервера от 1: макс %.2e", norm_deviation.max())
    log.info("top-1 совпал с эталоном: %d из %d", top1_same, total)
    log.info(
        "Справка, top-1 по разметке: сервер %d из %d, эталон %d из %d",
        server_hits,
        total,
        reference_hits,
        total,
    )
    for i, (s, r) in enumerate(zip(server_top1, reference_top1, strict=True)):
        if s != r:
            log.info(
                "  расхождение: «%s» → сервер %s, эталон %s",
                corpus.queries[i],
                corpus.fragment_ids[s],
                corpus.fragment_ids[r],
            )

    # Через np.all: NaN не проходит сравнение и роняет проверку.
    passed = bool(
        np.all(np.concatenate([query_cos, fragment_cos]) >= MIN_COSINE)
        and np.all(norm_deviation <= MAX_NORM_DEVIATION)
        and top1_same == total
    )
    if passed:
        log.info("Проверка пройдена.")
    else:
        log.error(
            "Проверка НЕ пройдена: нужны косинус ≥ %s, отклонение нормы ≤ %s, "
            "top-1 у всех вопросов.",
            MIN_COSINE,
            MAX_NORM_DEVIATION,
        )
    return passed


def main() -> None:
    """Точка входа: --base-url — адрес узла llama-server, без /v1."""
    configure_logging()
    parser = argparse.ArgumentParser(description="Сверка llama-server с эталоном")
    parser.add_argument("--base-url", required=True, help="например http://100.x.y.z:8001")
    args = parser.parse_args()
    sys.exit(0 if asyncio.run(run(args.base_url)) else 1)


if __name__ == "__main__":
    main()
