"""Подсказки для ASR: названия препаратов, которые модель без подсказки выбрасывает.

Beam search sherpa-onnx без `hotwords` молча теряет незнакомое название целиком
(ловушка в CLAUDE.md). Подсказка — одно слово в нужном регистре, поэтому составные
названия режутся до первого слова.
"""

from functools import lru_cache
from pathlib import Path

import clin_compass_knowledge
from clin_compass_knowledge.loader import load_knowledge


@lru_cache(maxsize=4)
def drug_hotwords(root: Path | None) -> tuple[str, ...]:
    """Торговые названия препаратов справочника `knowledge/` по одному слову, без повторов.

    Args:
        root: Каталог `knowledge/`; `None` — рабочая копия репозитория.
    """
    knowledge = load_knowledge(root or _repository_knowledge_dir())
    words: list[str] = []
    for drug in knowledge.drugs.values():
        first = drug.trade_name.split()[0].strip(",;.")
        if first and first not in words:
            words.append(first)
    return tuple(words)


def _repository_knowledge_dir() -> Path:
    """knowledge/ рабочей копии: пакет лежит в knowledge/src/…"""
    return Path(clin_compass_knowledge.__file__).resolve().parents[2]
