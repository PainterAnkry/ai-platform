"""Retrieval pipeline: query embedding → vector search → rerank.

This is the retrieval half of the framework's RAG flow:

    用户问题 → Query Embedding → Top-K 向量检索 → Reranker → Prompt → LLM

The prompt/LLM half lives in :mod:`app.rag.llm` and
:mod:`app.services.rag_service`; keeping retrieval separate makes it reusable
by the search API (no LLM call) and by the Agent's knowledge tool in Phase 5.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.knowledge import Document
from app.rag.embeddings import EmbeddingProvider, get_embedding_provider
from app.rag.reranker import Reranker, get_reranker
from app.repositories.document_repo import DocumentRepository

logger = logging.getLogger(__name__)

# Source snippets are trimmed for API responses / prompt overviews.
SNIPPET_MAX_CHARS = 240


@dataclass
class RetrievedChunk:
    """A scored retrieval result with everything needed for citation."""

    chunk_id: int
    document_id: int
    filename: str
    page_number: Optional[int]
    chunk_index: int
    content: str
    vector_score: float
    rerank_score: Optional[float] = None
    final_score: float = 0.0
    metadata: dict = field(default_factory=dict)

    @property
    def source(self) -> str:
        """Human-readable locator, e.g. ``产品手册.pdf · 第 3 页``."""
        if self.page_number is not None:
            return f"{self.filename} · 第 {self.page_number} 页"
        return self.filename

    @property
    def snippet(self) -> str:
        text = " ".join(self.content.split())
        if len(text) <= SNIPPET_MAX_CHARS:
            return text
        return text[: SNIPPET_MAX_CHARS - 1] + "…"


class RetrievalPipeline:
    """Embeds the query, pulls vector candidates, reranks, trims to top-K."""

    def __init__(
        self,
        db: Session,
        *,
        embedding: Optional[EmbeddingProvider] = None,
        reranker: Optional[Reranker] = None,
    ) -> None:
        self.doc_repo = DocumentRepository(db)
        self.embedding = embedding or get_embedding_provider()
        self.reranker = reranker if reranker is not None else get_reranker()

    @property
    def reranker_name(self) -> str:
        """Display name for API responses (``none`` = vector order kept)."""
        return self.reranker.name if self.reranker is not None else "none"

    def retrieve(
        self,
        kb_id: int,
        query: str,
        *,
        vector_top_k: Optional[int] = None,
        final_top_k: Optional[int] = None,
    ) -> list[RetrievedChunk]:
        """Return the best chunks for ``query`` inside one knowledge base.

        ``vector_top_k`` candidates come from the vector index; the reranker
        re-scores them and only ``final_top_k`` survive. Raises
        :class:`~app.rag.embeddings.EmbeddingError` /
        :class:`~app.rag.reranker.RerankError` on provider failure — the API
        layer maps those to a 502.
        """
        vector_k = vector_top_k or settings.RAG_VECTOR_TOP_K
        final_k = final_top_k or settings.rag_final_top_k

        query_vector = self.embedding.embed_query(query)
        hits = self.doc_repo.search_vectors(kb_id, query_vector, vector_k)
        if not hits:
            return []

        documents = self._documents_for(hits)

        candidates = [
            {
                "chunk_id": hit.chunk_id,
                "document_id": hit.document_id,
                "content": hit.content,
                "page_number": hit.page_number,
                "chunk_index": hit.chunk_index,
                "vector_score": hit.score,
            }
            for hit in hits
        ]
        # No reranker configured (``RERANKER_PROVIDER=none``): rerank scores
        # stay None and the vector order survives untouched.
        rerank_scores: list[Optional[float]] = (
            self.reranker.rerank(query, candidates)
            if self.reranker is not None
            else [None] * len(candidates)
        )

        reranked: list[RetrievedChunk] = []
        for candidate, rerank_score in zip(candidates, rerank_scores):
            document = documents.get(candidate["document_id"])
            reranked.append(
                RetrievedChunk(
                    chunk_id=candidate["chunk_id"],
                    document_id=candidate["document_id"],
                    filename=document.filename if document else "(已删除文档)",
                    page_number=candidate["page_number"],
                    chunk_index=candidate["chunk_index"],
                    content=candidate["content"],
                    vector_score=candidate["vector_score"],
                    rerank_score=rerank_score,
                    final_score=(
                        rerank_score
                        if rerank_score is not None
                        else candidate["vector_score"]
                    ),
                )
            )

        reranked.sort(key=lambda chunk: chunk.final_score, reverse=True)
        return reranked[:final_k]

    def _documents_for(self, hits: list) -> dict[int, Document]:
        """Load the parent documents once per distinct id for filenames."""
        documents: dict[int, Document] = {}
        for hit in hits:
            if hit.document_id not in documents:
                document = self.doc_repo.get_by_id(hit.document_id)
                if document is not None:
                    documents[hit.document_id] = document
        return documents
