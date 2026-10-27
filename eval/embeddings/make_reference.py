"""Считает эталон для check.py: векторы sentence-transformers fp32 (спека #16, раздел 5).

Запускается один раз и после каждой смены корпуса или модели. Нужно ~12 ГБ памяти: на Маке 36 ГБ
LLM на это время выключают.

    uv run --package clin-compass-eval --group reference python eval/embeddings/make_reference.py

Удалённый код модели (trust_remote_code) берётся на закреплённой ревизии из versions.env.
"""

import logging
import platform
from datetime import UTC, datetime

import numpy as np
import sentence_transformers
import torch
import transformers
from sentence_transformers import SentenceTransformer

from common import configure_logging, corpus_sha256, load_corpus, read_versions, save_reference

log = logging.getLogger(__name__)


def main() -> None:
    """Считает и сохраняет эталонные векторы вопросов и фрагментов корпуса."""
    configure_logging()
    versions = read_versions()
    corpus = load_corpus()
    device = "mps" if torch.backends.mps.is_available() else "cpu"

    log.info("Модель %s@%s, %s, fp32", versions["MODEL_REPO"], versions["MODEL_REVISION"], device)
    model = SentenceTransformer(
        versions["MODEL_REPO"],
        revision=versions["MODEL_REVISION"],
        trust_remote_code=True,
        device=device,
        model_kwargs={"dtype": torch.float32},
    )
    # По одному тексту: эталон не зависит от выравнивания длин внутри батча.
    # Префиксы — из config_sentence_transformers.json модели, наш клиент здесь не участвует.
    queries = model.encode(
        corpus.queries, prompt_name="query", batch_size=1, normalize_embeddings=True
    )
    fragments = model.encode(
        corpus.fragments, prompt_name="document", batch_size=1, normalize_embeddings=True
    )

    meta = {
        "model_repo": versions["MODEL_REPO"],
        "model_revision": versions["MODEL_REVISION"],
        "corpus_sha256": corpus_sha256(),
        "sentence_transformers": sentence_transformers.__version__,
        "transformers": transformers.__version__,
        "torch": torch.__version__,
        "device": device,
        "dtype": "float32",
        "machine": platform.machine(),
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    save_reference(np.asarray(queries, np.float32), np.asarray(fragments, np.float32), meta)
    log.info(
        "Эталон: %d вопросов, %d фрагментов, размерность %d",
        len(queries),
        len(fragments),
        queries.shape[1],
    )


if __name__ == "__main__":
    main()
