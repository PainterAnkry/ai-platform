"""RAG retrieval / query Pydantic schemas (Phase 3)."""

from __future__ import annotations

from typing import Optional

from pydantic import Field, field_validator

from app.schemas.common import ORMModel

# Question length cap: long pastes should go through documents, not the
# query box.
MAX_QUERY_LENGTH = 2000


class RagQueryRequest(ORMModel):
    """Shared input for vector search and RAG Q&A."""

    query: str = Field(min_length=1, max_length=MAX_QUERY_LENGTH)
    # None = use the configured pipeline defaults (RAG_VECTOR_TOP_K / FINAL).
    top_k: Optional[int] = Field(default=None, ge=1, le=50)

    @field_validator("query")
    @classmethod
    def _strip_query(cls, value: str) -> str:
        """Reject whitespace-only queries and trim the rest."""
        stripped = value.strip()
        if not stripped:
            raise ValueError("query must not be blank")
        return stripped


class SearchRequest(RagQueryRequest):
    """Input for the vector-search-only endpoint (no LLM call)."""


class RetrievedSource(ORMModel):
    """One retrieved chunk with its document locator and scores.

    ``index`` is the 1-based citation number shown to the model / rendered in
    the UI; for the search-only endpoint it is simply the rank.
    """

    index: int = Field(ge=1)
    document_id: int
    chunk_id: int
    filename: str
    page_number: Optional[int] = None
    chunk_index: int
    vector_score: float
    rerank_score: Optional[float] = None
    final_score: float
    snippet: str


class SearchResponse(ORMModel):
    query: str
    embedding_model: str
    reranker: str
    hits: list[RetrievedSource]
    duration_ms: int


class RagQueryResponse(ORMModel):
    """Answer + grounded sources for one RAG question."""

    question: str
    answer: str
    llm_provider: str
    embedding_model: str
    reranker: str
    sources: list[RetrievedSource]
    vector_candidates: int
    duration_ms: int
