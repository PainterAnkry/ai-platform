"""Phase 3 RAG endpoints: vector search + grounded Q&A with citations."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentUser, DBSession
from app.rag.embeddings import EmbeddingError
from app.rag.llm import LLMError
from app.rag.reranker import RerankError
from app.schemas.rag import RagQueryRequest, RagQueryResponse, SearchRequest, SearchResponse
from app.services.rag_service import RagService

router = APIRouter(prefix="/knowledge-bases", tags=["rag"])

# Failures of the configured AI providers surface as 502 so the frontend can
# tell "no results" (200, empty list) apart from "the embedding/LLM backend is
# misconfigured or unreachable".
_PROVIDER_ERRORS = (EmbeddingError, RerankError, LLMError)


@router.post(
    "/{kb_id}/search",
    response_model=SearchResponse,
    summary="Vector search inside a knowledge base (no LLM call)",
)
def search_knowledge_base(
    kb_id: int,
    payload: SearchRequest,
    db: DBSession,
    current_user: CurrentUser,
) -> SearchResponse:
    try:
        return RagService(db).search(kb_id, current_user.id, payload.query, payload.top_k)
    except _PROVIDER_ERRORS as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"AI provider failed: {exc}",
        ) from exc


@router.post(
    "/{kb_id}/query",
    response_model=RagQueryResponse,
    summary="RAG Q&A: retrieve, ground the prompt, answer with citations",
)
def query_knowledge_base(
    kb_id: int,
    payload: RagQueryRequest,
    db: DBSession,
    current_user: CurrentUser,
) -> RagQueryResponse:
    try:
        return RagService(db).query(kb_id, current_user.id, payload.query, payload.top_k)
    except _PROVIDER_ERRORS as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"AI provider failed: {exc}",
        ) from exc
