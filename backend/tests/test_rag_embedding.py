"""Embedding provider behaviour: determinism, dimensions, API contract."""

from __future__ import annotations

import math

import httpx
import pytest

from app.core.config import settings
from app.rag.embeddings import (
    EmbeddingError,
    MockEmbeddingProvider,
    OpenAICompatibleEmbeddingProvider,
    get_embedding_provider,
)


def _norm(vector: list[float]) -> float:
    return math.sqrt(sum(x * x for x in vector))


def test_mock_provider_is_deterministic_and_normalized() -> None:
    provider = MockEmbeddingProvider(dim=128)

    first = provider.embed_query("机器学习是人工智能的分支")
    second = provider.embed_query("机器学习是人工智能的分支")
    assert first == second, "same text must embed identically across calls"
    assert len(first) == 128
    assert _norm(first) == pytest.approx(1.0, abs=1e-9)


def test_mock_provider_similar_text_scores_higher_than_unrelated() -> None:
    provider = MockEmbeddingProvider()

    query = provider.embed_query("向量数据库 pgvector 检索")
    similar = provider.embed_query("pgvector 向量检索数据库")
    unrelated = provider.embed_query("今天天气晴朗适合散步")

    def dot(a: list[float], b: list[float]) -> float:
        return sum(x * y for x, y in zip(a, b))

    assert dot(query, similar) > dot(query, unrelated)


def test_mock_provider_batch_matches_single() -> None:
    provider = MockEmbeddingProvider()
    texts = ["第一段文本", "Second piece of text", "第三段：RAG 检索"]

    batch = provider.embed_documents(texts)
    assert [provider.embed_query(text) for text in texts] == batch


def test_openai_provider_request_shape_and_ordering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}

    def fake_post(url: str, **kwargs):
        captured["url"] = url
        captured["headers"] = kwargs.get("headers")
        captured["payload"] = kwargs.get("json")
        # Return embeddings out of order to prove index-based reassembly.
        payload = kwargs["json"]
        n = len(payload["input"])
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": n - 1 - i, "embedding": [1.0] + [0.0] * 7}
                    for i in range(n)
                ]
            },
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    provider = OpenAICompatibleEmbeddingProvider(
        api_base="https://api.example.com/v1",
        api_key="sk-test",
        model="text-embedding-test",
        timeout=5,
    )
    monkeypatch.setattr(settings, "EMBEDDING_DIM", 8, raising=False)

    vectors = provider.embed_documents(["a", "b", "c"])

    assert captured["url"] == "https://api.example.com/v1/embeddings"
    assert captured["headers"]["Authorization"] == "Bearer sk-test"
    assert captured["payload"]["model"] == "text-embedding-test"
    assert captured["payload"]["input"] == ["a", "b", "c"]
    # Results were reassembled by index, not by arrival order.
    assert vectors[0][0] == 1.0 and vectors[2][0] == 1.0


def test_openai_provider_batches_large_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []

    def fake_post(url: str, **kwargs):
        calls.append(len(kwargs["json"]["input"]))
        n = len(kwargs["json"]["input"])
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": i, "embedding": [0.5] * 8} for i in range(n)
                ]
            },
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(settings, "EMBEDDING_DIM", 8, raising=False)
    provider = OpenAICompatibleEmbeddingProvider(
        api_base="https://api.example.com/v1",
        api_key="sk-test",
        model="m",
        batch_size=4,
    )

    vectors = provider.embed_documents([f"doc {i}" for i in range(10)])

    assert calls == [4, 4, 2]
    assert len(vectors) == 10


def test_openai_provider_surfaces_api_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_post(url: str, **kwargs):
        return httpx.Response(401, json={"error": "bad key"})

    monkeypatch.setattr(httpx, "post", fake_post)
    provider = OpenAICompatibleEmbeddingProvider(
        api_base="https://api.example.com/v1", api_key="bad", model="m"
    )

    with pytest.raises(EmbeddingError, match="401"):
        provider.embed_query("hello")


def test_openai_provider_rejects_wrong_dimension(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_post(url: str, **kwargs):
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.1] * 5}]})

    monkeypatch.setattr(httpx, "post", fake_post)
    provider = OpenAICompatibleEmbeddingProvider(
        api_base="https://api.example.com/v1", api_key="k", model="m"
    )

    with pytest.raises(EmbeddingError, match="dim 5"):
        provider.embed_query("hello")


def test_openai_provider_requires_configuration() -> None:
    provider = OpenAICompatibleEmbeddingProvider(api_base="", api_key="", model="")
    with pytest.raises(EmbeddingError, match="EMBEDDING_API_BASE"):
        provider.embed_query("hello")


def test_factory_selects_provider_from_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    get_embedding_provider.cache_clear()
    monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "mock", raising=False)
    try:
        assert isinstance(get_embedding_provider(), MockEmbeddingProvider)

        # Clear the cache again so the next call re-reads the setting.
        get_embedding_provider.cache_clear()
        monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "bogus", raising=False)
        with pytest.raises(EmbeddingError, match="bogus"):
            get_embedding_provider()
    finally:
        get_embedding_provider.cache_clear()
