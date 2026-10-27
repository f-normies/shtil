"""Ввод-вывод подбора (спека #70, 5.1): HTTP-клиенты моделей и источник выдач из кэша."""

import json
from pathlib import Path

import httpx
import numpy as np
import pytest

from cache import JsonStore, VectorStore, text_key
from retrieval import Config, Doc
from tune_io import (
    EMBEDDING_MODELS,
    CacheSources,
    embed_texts,
    rerank_scores,
    vector_texts,
)


def _transport(handler):  # type: ignore[no-untyped-def]
    return httpx.MockTransport(handler)


@pytest.mark.anyio
async def test_embed_texts_prefixes_queries_and_batches() -> None:
    requests: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        data = [{"index": i, "embedding": [float(i), 1.0]} for i in range(len(body["input"]))]
        return httpx.Response(200, json={"data": data})

    async with httpx.AsyncClient(transport=_transport(handler), base_url="http://m") as client:
        vectors = await embed_texts(
            client, EMBEDDING_MODELS["qwen3-emb-4b"], ["q1", "q2", "q3"], query=True, batch=2
        )

    assert [len(r["input"]) for r in requests] == [2, 1]  # type: ignore[arg-type]
    assert requests[0]["input"][0].startswith("Instruct: ")  # type: ignore[index]
    assert requests[0]["input"][0].endswith("\nQuery:q1")  # type: ignore[index]
    assert vectors[2].tolist() == [0.0, 1.0]


@pytest.mark.anyio
async def test_embed_texts_leaves_documents_and_bge_queries_as_is() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.extend(body["input"])
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0]}]})

    async with httpx.AsyncClient(transport=_transport(handler), base_url="http://m") as client:
        await embed_texts(client, EMBEDDING_MODELS["giga-3b"], ["doc"], query=False, batch=8)
        await embed_texts(client, EMBEDDING_MODELS["bge-m3"], ["q"], query=True, batch=8)

    assert seen == ["doc", "q"]


@pytest.mark.anyio
async def test_rerank_scores_follow_document_order() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["query"] == "q" and body["top_n"] == 2
        results = [{"index": 1, "relevance_score": 0.9}, {"index": 0, "relevance_score": 0.1}]
        return httpx.Response(200, json={"results": results})

    async with httpx.AsyncClient(transport=_transport(handler), base_url="http://r") as client:
        assert await rerank_scores(client, "q", ["d0", "d1"]) == [0.1, 0.9]


def _docs() -> dict[str, Doc]:
    return {
        "a": Doc(key="a", source_id="s", title="T", heading_path="H", text="альфа"),
        "b": Doc(key="b", source_id="s", title="T", heading_path="H", text="бета"),
    }


def test_vector_texts_cover_whole_and_parts_with_context() -> None:
    docs = _docs()

    texts = vector_texts(docs, contexts={"a": "контекст"}, parts=False, context=True)

    assert texts["a"] == ["T. H\nконтекст\nальфа"]
    assert texts["b"] == ["T. H\nбета"]


def test_cache_sources_rank_by_cached_vectors(tmp_path: Path) -> None:
    store = VectorStore(tmp_path / "embeddings" / "giga-3b.npz")
    store.put(
        {
            text_key("doc", "T. H\nальфа"): np.array([1.0, 0.0]),
            text_key("doc", "T. H\nбета"): np.array([0.0, 1.0]),
            text_key("query", "вопрос"): np.array([0.1, 0.9]),
        }
    )
    sources = CacheSources(
        docs=_docs(), cache_dir=tmp_path, contexts={}, full_text_rankings={}, strict=True
    )

    assert sources.vector(Config(), "вопрос") == ["b", "a"]


def test_cache_sources_collect_missing_rerank_pairs(tmp_path: Path) -> None:
    JsonStore(tmp_path / "rerank" / "bge-v2-m3.json").put(
        {f"{text_key('query', 'q')}|{text_key('doc', 'T. H\nальфа')}": 0.7}
    )
    collecting = CacheSources(
        docs=_docs(), cache_dir=tmp_path, contexts={}, full_text_rankings={}, strict=False
    )

    scores = collecting.rerank("bge-v2-m3", "q", ["a", "b"])

    assert scores == {"a": 0.7, "b": 0.0}
    assert collecting.wanted == {"bge-v2-m3": {("q", "b")}}

    strict = CacheSources(
        docs=_docs(), cache_dir=tmp_path, contexts={}, full_text_rankings={}, strict=True
    )
    with pytest.raises(KeyError, match="bge-v2-m3"):
        strict.rerank("bge-v2-m3", "q", ["a", "b"])
