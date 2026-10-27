"""Подсказки ASR: торговые названия препаратов справочника (ловушка в CLAUDE.md про beam search)."""

import re

from clin_compass.core.ai.hotwords import drug_hotwords

_ONE_WORD = re.compile(r"^[\w-]+$")


def test_hotwords_include_aviandr_as_a_single_capitalised_word() -> None:
    words = drug_hotwords(None)

    assert "Авиандр" in words
    assert all(_ONE_WORD.match(word) for word in words)
    assert len(words) == len(set(words))


def test_hotwords_are_cached_per_root() -> None:
    assert drug_hotwords(None) is drug_hotwords(None)
