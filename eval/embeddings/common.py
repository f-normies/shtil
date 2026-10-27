"""Общее для скриптов eval/embeddings: корпус, версии сервиса, эталон, клиент."""

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
from clin_compass.core.ai.embeddings import build_embedder
from clin_compass.core.ai.runs import NullRecorder
from clin_compass.core.config import Settings
from pydantic_ai import Embedder

HERE = Path(__file__).resolve().parent
CORPUS_PATH = HERE / "corpus.json"
REFERENCE_PATH = HERE / "reference.npz"
VERSIONS_PATH = HERE.parents[1] / "services" / "embeddings" / "versions.env"

Matrix = npt.NDArray[np.float32]


@dataclass(frozen=True)
class Corpus:
    """Фрагменты и вопросы; у вопроса — id фрагмента, который должен найтись первым."""

    fragment_ids: list[str]
    fragments: list[str]
    queries: list[str]
    expected_ids: list[str]


@dataclass(frozen=True)
class Reference:
    """Эталонные векторы sentence-transformers и условия, в которых их посчитали."""

    queries: Matrix
    fragments: Matrix
    meta: dict[str, Any]


def configure_logging() -> None:
    """Отчёт скриптов — строки лога без служебных полей; HTTP-запросы клиента не выводим."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    # OpenAI SDK 3.x ходит в сеть через httpx2, остальные библиотеки — через httpx.
    for name in ("httpx", "httpx2"):
        logging.getLogger(name).setLevel(logging.WARNING)


def corpus_sha256() -> str:
    """SHA-256 файла корпуса: эталон действителен только для того корпуса, по которому посчитан."""
    return hashlib.sha256(CORPUS_PATH.read_bytes()).hexdigest()


def load_corpus() -> Corpus:
    """Читает corpus.json."""
    data = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    return Corpus(
        fragment_ids=[item["id"] for item in data["fragments"]],
        fragments=[item["text"] for item in data["fragments"]],
        queries=[item["text"] for item in data["queries"]],
        expected_ids=[item["fragment_id"] for item in data["queries"]],
    )


def read_versions() -> dict[str, str]:
    """Читает services/embeddings/versions.env (строки KEY=VALUE, # — комментарий)."""
    versions: dict[str, str] = {}
    for line in VERSIONS_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            key, _, value = line.partition("=")
            versions[key] = value
    return versions


def save_reference(queries: Matrix, fragments: Matrix, meta: dict[str, Any]) -> None:
    """Пишет эталон в reference.npz; метаданные — JSON-строкой."""
    np.savez_compressed(
        REFERENCE_PATH, queries=queries, fragments=fragments, meta=np.array(json.dumps(meta))
    )


def load_reference() -> Reference:
    """Читает reference.npz."""
    with np.load(REFERENCE_PATH) as data:
        return Reference(
            queries=data["queries"],
            fragments=data["fragments"],
            meta=json.loads(str(data["meta"])),
        )


def normalize(vectors: Matrix) -> Matrix:
    """L2-нормализация по строкам: после неё косинус — скалярное произведение."""
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    return (vectors / norms).astype(np.float32)


def embedder_for(base_url: str) -> Embedder:
    """Клиент core/ai для узла base_url (`http://<адрес>:8001`). Тот же код, что у приложения."""
    # БД скриптам не нужна; адрес-заглушка, как в clin_compass.openapi. Вызовы в ai_runs не пишутся.
    settings = Settings(
        database_url="postgresql+asyncpg://unused/none", embeddings_urls=(base_url,)
    )
    return build_embedder(settings, NullRecorder(), task="eval.embeddings")
