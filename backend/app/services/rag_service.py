"""RAG business logic: ownership-checked vector search and grounded Q&A.

Thin orchestration layer: ownership comes from
:class:`~app.services.knowledge_base_service.KnowledgeBaseService`, retrieval
from :class:`~app.rag.retrieval.RetrievalPipeline`, generation from
:class:`~app.rag.llm.LLMProvider`. The service never talks SQL or HTTP itself.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.rag.llm import ContextBlock, get_llm_provider
from app.rag.retrieval import RetrievedChunk, RetrievalPipeline
from app.schemas.rag import RagQueryResponse, RetrievedSource, SearchResponse
from app.services.knowledge_base_service import KnowledgeBaseService

logger = logging.getLogger(__name__)


class RagService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.kb_service = KnowledgeBaseService(db)

    # ---------- vector search (no LLM) ----------
    def search(
        self,
        kb_id: int,
        user_id: int,
        query: str,
        top_k: Optional[int] = None,
    ) -> SearchResponse:
        """Top-K chunks of an owned knowledge base for ``query``."""
        self.kb_service.get_for_user(kb_id, user_id)  # 404 if not owned

        started = time.perf_counter()
        pipeline = RetrievalPipeline(self.db)
        chunks = pipeline.retrieve(
            kb_id, query, final_top_k=top_k or settings.rag_final_top_k
        )
        duration_ms = int((time.perf_counter() - started) * 1000)

        return SearchResponse(
            query=query,
            embedding_model=pipeline.embedding.signature,
            reranker=pipeline.reranker_name,
            hits=[_to_source(chunk, index=rank) for rank, chunk in enumerate(chunks, 1)],
            duration_ms=duration_ms,
        )

    # ---------- grounded Q&A ----------
    def query(
        self,
        kb_id: int,
        user_id: int,
        question: str,
        top_k: Optional[int] = None,
    ) -> RagQueryResponse:
        """Retrieve, build the grounded prompt, ask the LLM, attach sources.

        Synchronous by design in Phase 3; Phase 4 swaps the generation step
        for an SSE stream without touching retrieval.
        """
        self.kb_service.get_for_user(kb_id, user_id)  # 404 if not owned

        started = time.perf_counter()
        pipeline = RetrievalPipeline(self.db)
        chunks = pipeline.retrieve(
            kb_id, question, final_top_k=top_k or settings.rag_final_top_k
        )

        blocks = [
            ContextBlock(index=rank, source=chunk.source, content=chunk.content)
            for rank, chunk in enumerate(chunks, 1)
        ]
        llm = get_llm_provider()
        answer = llm.rag_answer(question, blocks)
        duration_ms = int((time.perf_counter() - started) * 1000)

        sources = [
            _to_source(chunk, index=rank) for rank, chunk in enumerate(chunks, 1)
        ]
        logger.info(
            "RAG query on kb=%s: %d candidates -> %d sources, llm=%s, %dms",
            kb_id,
            len(chunks),
            len(sources),
            llm.signature,
            duration_ms,
        )
        return RagQueryResponse(
            question=question,
            answer=answer,
            llm_provider=llm.signature,
            embedding_model=pipeline.embedding.signature,
            reranker=pipeline.reranker_name,
            sources=sources,
            vector_candidates=len(chunks),
            duration_ms=duration_ms,
        )


def _to_source(chunk: RetrievedChunk, *, index: int) -> RetrievedSource:
    """Map a retrieval result onto the API schema."""
    return RetrievedSource(
        index=index,
        document_id=chunk.document_id,
        chunk_id=chunk.chunk_id,
        filename=chunk.filename,
        page_number=chunk.page_number,
        chunk_index=chunk.chunk_index,
        vector_score=round(chunk.vector_score, 6),
        rerank_score=(
            round(chunk.rerank_score, 6)
            if chunk.rerank_score is not None
            else None
        ),
        final_score=round(chunk.final_score, 6),
        snippet=chunk.snippet,
    )
