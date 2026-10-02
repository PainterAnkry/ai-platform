"""Embedding provider abstraction.

RAG must not be bound to a single model vendor (framework principle: LLM /
embedding calls go through a Provider abstraction layer). Three
implementations ship out of the box:

* :class:`MockEmbeddingProvider` — deterministic character n-gram hashing.
  No network, no dependencies; good enough to demo retrieval offline and the
  default for tests and the zero-install preview.
* :class:`OpenAICompatibleEmbeddingProvider` — calls any ``/v1/embeddings``
  style endpoint (OpenAI, SiliconFlow, Zhipu, ...).
* :class:`LocalEmbeddingProvider` — sentence-transformers, imported lazily so
  the dependency (and torch) stays optional.

The provider is selected once via :func:`get_embedding_provider`, keyed on
``settings.EMBEDDING_PROVIDER``. Callers only see ``list[list[float]]``
vectors of length ``settings.EMBEDDING_DIM``.
"""

from __future__ import annotations

import hashlib
import logging
import math
from abc import ABC, abstractmethod
from functools import lru_cache
from typing import Sequence

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

# Errors surface as 400/502 to the API layer, not as a provider crash.
class EmbeddingError(RuntimeError):
    """Raised when an embedding provider fails or returns unusable output."""


class EmbeddingProvider(ABC):
    """Common interface for turning text into fixed-width vectors."""

    name: str = "base"

    @abstractmethod
    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed a batch of documents/chunks."""

    @abstractmethod
    def embed_query(self, text: str) -> list[float]:
        """Embed a single user query."""

    @property
    def signature(self) -> str:
        """Identity stored on ``documents.embedding_model`` for provenance."""
        return f"{self.name}:{settings.EMBEDDING_DIM}"

    # ---------- shared helpers ----------
    @staticmethod
    def _validate_dim(vector: Sequence[float]) -> list[float]:
        dim = settings.EMBEDDING_DIM
        values = [float(x) for x in vector]
        if len(values) != dim:
            raise EmbeddingError(
                f"Embedding provider returned dim {len(values)}, expected {dim} "
                "(check EMBEDDING_DIM vs the configured model)"
            )
        return values

    @staticmethod
    def _normalize(vector: list[float]) -> list[float]:
        """Unit-length vector so cosine similarity reduces to a dot product."""
        norm = math.sqrt(sum(x * x for x in vector))
        if norm == 0.0:
            return vector
        return [x / norm for x in vector]


def _char_ngrams(text: str, sizes: Sequence[int] = (2, 3)) -> list[str]:
    """Character n-grams covering both CJK and latin scripts.

    CJK has no whitespace tokens, so character n-grams are the language-neutral
    equivalent of word hashing; for latin text they degrade gracefully to
    syllable-like features. Lowercasing keeps 'PDF' and 'pdf' identical.
    """
    normalized = " ".join(text.lower().split())
    grams: list[str] = []
    for size in sizes:
        if len(normalized) < size:
            grams.append(normalized)
            continue
        for i in range(len(normalized) - size + 1):
            grams.append(normalized[i : i + size])
    return grams


class MockEmbeddingProvider(EmbeddingProvider):
    """Deterministic hashed bag-of-n-grams embedding.

    Each n-gram is hashed to ``hashlib.blake2b`` digest; the digest picks a
    bucket and a sign, so the vector is stable across processes and platforms.
    Vectors are L2-normalised, making cosine similarity a plain dot product.
    This is intentionally lexical — it demonstrates the full pgvector pipeline
    without any API key, but real semantic quality needs a real model.
    """

    name = "mock"

    def __init__(self, dim: int | None = None) -> None:
        self.dim = dim or settings.EMBEDDING_DIM

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed_one(text)

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self.dim
        for gram in _char_ngrams(text):
            digest = hashlib.blake2b(gram.encode("utf-8"), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "big") % self.dim
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[index] += sign
        return self._normalize(vector)


class OpenAICompatibleEmbeddingProvider(EmbeddingProvider):
    """Client for any OpenAI-compatible ``POST {base}/embeddings`` endpoint.

    ``base`` should be the root (e.g. ``https://api.siliconflow.cn/v1``); the
    client appends ``/embeddings``. Requests are batched to keep large
    documents inside per-request limits.
    """

    name = "openai"

    def __init__(
        self,
        api_base: str,
        api_key: str,
        model: str,
        batch_size: int | None = None,
        timeout: float | None = None,
    ) -> None:
        self.api_base = api_base.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.batch_size = batch_size or settings.EMBEDDING_BATCH_SIZE
        self.timeout = timeout or settings.LLM_TIMEOUT_SECONDS

    @property
    def signature(self) -> str:
        return f"openai:{self.model}"

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = list(texts[start : start + self.batch_size])
            vectors.extend(self._request(batch))
        return vectors

    def embed_query(self, text: str) -> list[float]:
        return self._request([text])[0]

    def _request(self, batch: list[str]) -> list[list[float]]:
        if not self.api_base or not self.api_key or not self.model:
            raise EmbeddingError(
                "EMBEDDING_PROVIDER=openai requires EMBEDDING_API_BASE, "
                "EMBEDDING_API_KEY and EMBEDDING_MODEL"
            )
        try:
            response = httpx.post(
                f"{self.api_base}/embeddings",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={"model": self.model, "input": batch},
                timeout=self.timeout,
            )
        except httpx.HTTPError as exc:
            raise EmbeddingError(f"Embedding API request failed: {exc}") from exc

        if response.status_code >= 400:
            raise EmbeddingError(
                f"Embedding API returned {response.status_code}: "
                f"{response.text[:300]}"
            )

        payload = response.json()
        try:
            data = sorted(payload["data"], key=lambda item: item["index"])
            return [self._validate_dim(item["embedding"]) for item in data]
        except (KeyError, TypeError, ValueError) as exc:
            raise EmbeddingError(
                f"Embedding API returned an unexpected payload: {exc}"
            ) from exc


class LocalEmbeddingProvider(EmbeddingProvider):
    """sentence-transformers provider, imported only when actually selected.

    Keeping the import lazy means the default install (no torch) still boots;
    opting in is ``pip install sentence-transformers`` plus
    ``EMBEDDING_PROVIDER=local``.
    """

    name = "local"

    def __init__(self, model_name: str | None = None) -> None:
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore
        except ImportError as exc:  # pragma: no cover - depends on env
            raise EmbeddingError(
                "EMBEDDING_PROVIDER=local requires sentence-transformers: "
                "pip install sentence-transformers"
            ) from exc
        self.model_name = model_name or "paraphrase-multilingual-MiniLM-L12-v2"
        self._model = SentenceTransformer(self.model_name)
        logger.info("Loaded local embedding model %s", self.model_name)

    @property
    def signature(self) -> str:
        return f"local:{self.model_name}"

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        raw = self._model.encode(list(texts), normalize_embeddings=True)
        return [self._validate_dim(vector) for vector in raw]

    def embed_query(self, text: str) -> list[float]:
        raw = self._model.encode([text], normalize_embeddings=True)
        return self._validate_dim(raw[0])


@lru_cache(maxsize=1)
def get_embedding_provider() -> EmbeddingProvider:
    """Build the provider selected by ``EMBEDDING_PROVIDER`` (cached)."""
    provider = settings.EMBEDDING_PROVIDER
    if provider == "mock":
        return MockEmbeddingProvider()
    if provider == "openai":
        return OpenAICompatibleEmbeddingProvider(
            api_base=settings.EMBEDDING_API_BASE,
            api_key=settings.EMBEDDING_API_KEY,
            model=settings.EMBEDDING_MODEL,
        )
    if provider == "local":
        return LocalEmbeddingProvider()
    raise EmbeddingError(f"Unknown EMBEDDING_PROVIDER '{provider}'")
