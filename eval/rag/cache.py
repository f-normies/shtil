"""Кэш подбора на диске (спека #70, 5.1): векторы, оценки ранжировщика, строки контекста.

Каталог `eval/rag/cache/` в git не попадает: в нём производные от текста рекомендаций.
Ключ — хеш роли и текста (`text_key`): повторный запуск не пересчитывает готовое,
а изменённый текст получает новый ключ.
"""

import hashlib
import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import numpy as np

CACHE = Path(__file__).resolve().parent / "cache"


def text_key(role: str, text: str) -> str:
    """Ключ кэша: первые 24 знака SHA-256 от роли и текста."""
    return hashlib.sha256(f"{role}\n{text}".encode()).hexdigest()[:24]


class VectorStore:
    """Векторы одной модели эмбеддингов: ключ → float32. Файл `.npz` пишется целиком."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._vectors: dict[str, np.ndarray] = {}
        if path.exists():
            with np.load(path) as data:
                keys = [str(k) for k in data["keys"]]
                self._vectors = dict(zip(keys, data["vectors"], strict=True))

    def missing(self, keys: Iterable[str]) -> list[str]:
        """Ключи, которых нет в кэше, в порядке запроса без повторов."""
        return list(dict.fromkeys(k for k in keys if k not in self._vectors))

    def get(self, key: str) -> np.ndarray:
        return self._vectors[key]

    def put(self, vectors: Mapping[str, np.ndarray]) -> None:
        """Добавляет векторы и сохраняет файл."""
        for key, vector in vectors.items():
            self._vectors[key] = np.asarray(vector, dtype=np.float32)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        keys = list(self._vectors)
        np.savez(
            self._path,
            keys=np.array(keys),
            vectors=np.stack([self._vectors[k] for k in keys]),
        )


class JsonStore:
    """Значения по ключу в одном JSON: оценки ранжировщика, строки контекста."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._values: dict[str, Any] = (
            json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        )

    def missing(self, keys: Iterable[str]) -> list[str]:
        return list(dict.fromkeys(k for k in keys if k not in self._values))

    def get(self, key: str) -> Any:
        return self._values[key]

    def put(self, values: Mapping[str, Any]) -> None:
        self._values.update(values)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._values, ensure_ascii=False), encoding="utf-8")
