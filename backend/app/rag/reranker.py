"""Reranker provider abstraction.

Vector search (ANN) is fast but coarse. The RAG pipeline therefore pulls a
wider candidate set (``RAG_VECTOR_TOP_K``) and re-scores it with a reranker,
keeping only the best ``RAG_FINAL_TOP_K`` chunks for the prompt.

Implementations:

* :class:`LexicalReranker` — built-in character n-gram overlap scoring.
  Deterministic, offline, zero dependencies; the default.
* :class:`ApiReranker` — Jina/Cohere-style ``/rerank`` HTTP endpoint
  (SiliconFlow, Jina, ...).

``RERANKER_PROVIDER=none`` means no reranker: :func:`get_reranker` returns
``None`` and the retrieval pipeline keeps the vector-search order.

Rerankers never reorder or drop metadata: they receive
:class:`~app.rag.retrieval.RetrievedChunk`-shaped dicts and return scores
aligned with the input order.
"""

from __future__ import annotations

import logging
import math
from abc import ABC, abstractmethod
from functools import lru_cache
from typing import Any, Optional, Sequence

import httpx

from app.core.config import settings
from app.rag.embeddings import _char_ngrams

logger = logging.getLogger(__name__)


class RerankError(RuntimeError):
    """Raised when an external rerank endpoint fails."""


class Reranker(ABC):
    """Common interface for re-scoring retrieved chunks against a query."""

    name: str = "base"

    @abstractmethod
    def rerank(
        self, query: str, candidates: Sequence[dict[str, Any]]
    ) -> list[float]:
        """Return one relevance score per candidate, in input order."""


class LexicalReranker(Reranker):
    """Character n-gram overlap between the query and each chunk.

    Scores are cosine-similarity over weighted n-gram sets, so they land in
    [0, 1]. For CJK text this behaves like a soft keyword match — noticeably
    better than raw vector order for exact terms, and a sane default when no
    rerank model is configured.
    """

    name = "lexical"

    def rerank(
        self, query: str, candidates: Sequence[dict[str, Any]]
    ) -> list[float]:
        query_counts = self._count_grams(query)
        if not query_counts:
            return [0.0] * len(candidates)

        scores: list[float] = []
        for candidate in candidates:
            content = str(candidate.get("content") or "")
            chunk_counts = self._count_grams(content)
            scores.append(self._cosine(query_counts, chunk_counts))
        return scores

    @staticmethod
    def _count_grams(text: str) -> dict[str, float]:
        counts: dict[str, float] = {}
        for gram in _char_ngrams(text):
            counts[gram] = counts.get(gram, 0.0) + 1.0
        # Sublinear term weight: long chunks should not dominate purely by
        # repeating the same n-grams.
        return {gram: 1.0 + math.log(count) for gram, count in counts.items()}

    @staticmethod
    def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
        if not a or not b:
            return 0.0
        dot = sum(value * b.get(key, 0.0) for key, value in a.items())
        norm_a = math.sqrt(sum(value * value for value in a.values()))
        norm_b = math.sqrt(sum(value * value for value in b.values()))
        if norm_a == 0.0 or norm_b == 0.0:
            return 0.0
        return dot / (norm_a * norm_b)


class ApiReranker(Reranker):
    """Client for Jina/Cohere-style ``POST {base}/rerank`` endpoints.

    Request:  ``{"model": ..., "query": ..., "documents": [...]}``
    Response: ``{"results": [{"index": 0, "relevance_score": 0.98}, ...]}``

    The scores are mapped back to the caller's input order, so the pipeline
    code does not care whether the model reordered anything.
    """

    name = "api"

    def __init__(
        self,
        api_base: str,
        api_key: str,
        model: str,
        timeout: float | None = None,
    ) -> None:
        self.api_base = api_base.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout or settings.LLM_TIMEOUT_SECONDS

    def rerank(
        self, query: str, candidates: Sequence[dict[str, Any]]
    ) -> list[float]:
        if not self.api_base or not self.api_key or not self.model:
            raise RerankError(
                "RERANKER_PROVIDER=api requires RERANKER_API_BASE, "
                "RERANKER_API_KEY and RERANKER_MODEL"
            )
        if not candidates:
            return []

        documents = [str(item.get("content") or "") for item in candidates]
        try:
            response = httpx.post(
                f"{self.api_base}/rerank",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={"model": self.model, "query": query, "documents": documents},
                timeout=self.timeout,
            )
        except httpx.HTTPError as exc:
            raise RerankError(f"Rerank API request failed: {exc}") from exc

        if response.status_code >= 400:
            raise RerankError(
                f"Rerank API returned {response.status_code}: {response.text[:300]}"
            )

        payload = response.json()
        scores = [0.0] * len(candidates)
        try:
            for item in payload["results"]:
                index = int(item["index"])
                if 0 <= index < len(scores):
                    scores[index] = float(item.get("relevance_score", 0.0))
        except (KeyError, TypeError, ValueError) as exc:
            raise RerankError(f"Rerank API returned an unexpected payload: {exc}") from exc
        return scores


@lru_cache(maxsize=1)
def get_reranker() -> Optional[Reranker]:
    """Build the reranker selected by ``RERANKER_PROVIDER`` (cached).

    Returns ``None`` when ``RERANKER_PROVIDER=none`` — callers keep the raw
    vector order instead of paying for a pass-through "reranker".
    """
    provider = settings.RERANKER_PROVIDER
    if provider == "none":
        return None
    if provider == "lexical":
        return LexicalReranker()
    if provider == "api":
        return ApiReranker(
            api_base=settings.RERANKER_API_BASE,
            api_key=settings.RERANKER_API_KEY,
            model=settings.RERANKER_MODEL,
        )
    raise RerankError(f"Unknown RERANKER_PROVIDER '{provider}'")
