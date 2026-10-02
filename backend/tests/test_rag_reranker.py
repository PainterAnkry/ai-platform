"""Reranker behaviour: lexical scoring, factory selection, API contract."""

from __future__ import annotations

import httpx
import pytest

from app.rag.reranker import (
    ApiReranker,
    LexicalReranker,
    RerankError,
    get_reranker,
)

CANDIDATES = [
    {"content": "本季度销售额同比增长百分之二十，其中三月出现异常峰值。", "vector_score": 0.9},
    {"content": "机器学习是人工智能的一个分支，通过数据训练模型。", "vector_score": 0.8},
    {"content": " pgvector 是 PostgreSQL 的向量检索扩展。", "vector_score": 0.7},
]


def test_lexical_reranker_prefers_matching_content() -> None:
    reranker = LexicalReranker()

    scores = reranker.rerank("三月销售额为什么异常", CANDIDATES)

    assert len(scores) == len(CANDIDATES)
    assert scores[0] == max(scores), "sales chunk must outscore unrelated ones"
    assert all(0.0 <= score <= 1.0 for score in scores)


def test_lexical_reranker_handles_empty_query() -> None:
    reranker = LexicalReranker()
    assert reranker.rerank("", CANDIDATES) == [0.0, 0.0, 0.0]
    assert reranker.rerank("任何问题", []) == []


def test_factory_none_provider_returns_no_reranker() -> None:
    from app.core.config import settings

    get_reranker.cache_clear()
    original = settings.RERANKER_PROVIDER
    settings.RERANKER_PROVIDER = "none"
    try:
        assert get_reranker() is None
    finally:
        settings.RERANKER_PROVIDER = original
        get_reranker.cache_clear()


def test_api_reranker_maps_scores_back_to_input_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}

    def fake_post(url: str, **kwargs):
        captured["url"] = url
        captured["payload"] = kwargs.get("json")
        return httpx.Response(
            200,
            json={
                "results": [
                    {"index": 2, "relevance_score": 0.99},
                    {"index": 0, "relevance_score": 0.42},
                    {"index": 1, "relevance_score": 0.11},
                ]
            },
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    reranker = ApiReranker(
        api_base="https://api.example.com/v1", api_key="k", model="bge-reranker"
    )

    scores = reranker.rerank("查询", CANDIDATES)

    assert captured["url"] == "https://api.example.com/v1/rerank"
    assert captured["payload"]["query"] == "查询"
    assert captured["payload"]["documents"] == [c["content"] for c in CANDIDATES]
    assert scores == [0.42, 0.11, 0.99]


def test_api_reranker_surfaces_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(url: str, **kwargs):
        return httpx.Response(500, text="boom")

    monkeypatch.setattr(httpx, "post", fake_post)
    reranker = ApiReranker(api_base="https://x/v1", api_key="k", model="m")

    with pytest.raises(RerankError, match="500"):
        reranker.rerank("q", CANDIDATES)


def test_api_reranker_requires_configuration() -> None:
    reranker = ApiReranker(api_base="", api_key="", model="")
    with pytest.raises(RerankError, match="RERANKER_API_BASE"):
        reranker.rerank("q", CANDIDATES)


def test_factory_unknown_provider() -> None:
    from app.core.config import settings

    get_reranker.cache_clear()
    original = settings.RERANKER_PROVIDER
    settings.RERANKER_PROVIDER = "bogus"
    try:
        with pytest.raises(RerankError, match="bogus"):
            get_reranker()
    finally:
        settings.RERANKER_PROVIDER = original
        get_reranker.cache_clear()
