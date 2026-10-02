"""Retrieval pipeline + RAG service on SQLite (mock providers, real SQL).

Exercises the full Phase 3 read path against a real database: seeded chunks
are embedded with the deterministic mock provider, then searched / reranked /
answered through the same code the API routes call.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.knowledge import Document, DocumentChunk, KnowledgeBase
from app.models.user import User
from app.rag.embeddings import MockEmbeddingProvider
from app.rag.retrieval import RetrievalPipeline
from app.services.rag_service import RagService

DOC1_NAME = "机器学习入门.pdf"
DOC2_NAME = "销售报告.docx"

DOC1_CHUNKS = [
    (0, 1, "机器学习是人工智能的一个分支，它通过数据训练模型，从经验中学习。"),
    (1, 1, "深度学习是机器学习的子领域，使用多层神经网络拟合复杂函数。"),
    (2, 2, "pgvector 是 PostgreSQL 的开源向量检索扩展，支持 HNSW 索引。"),
]
DOC2_CHUNKS = [
    (0, 3, "本季度销售额同比增长百分之二十，三月出现异常峰值。"),
    (1, 4, "华东区贡献了最多的新增客户，华南区增速最快。"),
]


@pytest.fixture()
def seeded_kb(sqlite_session: Session) -> tuple[int, int, int]:
    """Two documents with mock-embedded chunks; returns (kb_id, doc1, doc2)."""
    owner = User(username="owner", email="owner@example.com", hashed_password="x")
    other = User(username="other", email="other@example.com", hashed_password="x")
    sqlite_session.add_all([owner, other])
    sqlite_session.commit()

    kb = KnowledgeBase(user_id=owner.id, name="RAG 测试库")
    sqlite_session.add(kb)
    sqlite_session.commit()

    doc1 = Document(
        knowledge_base_id=kb.id, user_id=owner.id, filename=DOC1_NAME,
        file_type="pdf", file_size=10, storage_path=f"{owner.id}/a.pdf",
        status="completed", chunk_count=len(DOC1_CHUNKS),
    )
    doc2 = Document(
        knowledge_base_id=kb.id, user_id=owner.id, filename=DOC2_NAME,
        file_type="docx", file_size=10, storage_path=f"{owner.id}/b.docx",
        status="completed", chunk_count=len(DOC2_CHUNKS),
    )
    sqlite_session.add_all([doc1, doc2])
    sqlite_session.commit()

    provider = MockEmbeddingProvider()
    rows: list[DocumentChunk] = []
    for doc, chunk_defs in ((doc1, DOC1_CHUNKS), (doc2, DOC2_CHUNKS)):
        for index, page, content in chunk_defs:
            rows.append(
                DocumentChunk(
                    document_id=doc.id,
                    knowledge_base_id=kb.id,
                    chunk_index=index,
                    content=content,
                    page_number=page,
                    token_estimate=len(content),
                    embedding=provider.embed_query(content),
                )
            )
    sqlite_session.add_all(rows)
    sqlite_session.commit()
    return kb.id, doc1.id, doc2.id


def test_vector_search_ranks_relevant_chunk_first(
    sqlite_session: Session, seeded_kb: tuple[int, int, int]
) -> None:
    kb_id, doc1_id, _ = seeded_kb

    response = RagService(sqlite_session).search(
        kb_id, user_id=1, query="pgvector 向量检索 HNSW", top_k=3
    )

    assert response.hits, "embedded chunks must be retrievable"
    assert "pgvector" in response.hits[0].snippet
    assert response.hits[0].document_id == doc1_id
    assert response.hits[0].page_number == 2
    assert 0.0 < response.hits[0].vector_score <= 1.0
    # Every hit carries a complete citation locator.
    for hit in response.hits:
        assert hit.filename and hit.chunk_index >= 0 and hit.index >= 1
    assert response.reranker == "lexical"


def test_search_without_reranker_keeps_vector_order(
    sqlite_session: Session, seeded_kb: tuple[int, int, int], monkeypatch
) -> None:
    from app.core.config import settings
    from app.rag.reranker import get_reranker

    monkeypatch.setattr(settings, "RERANKER_PROVIDER", "none", raising=False)
    get_reranker.cache_clear()
    try:
        kb_id, _, _ = seeded_kb

        response = RagService(sqlite_session).search(kb_id, user_id=1, query="销售额")

        assert response.reranker == "none"
        for hit in response.hits:
            assert hit.rerank_score is None
    finally:
        get_reranker.cache_clear()


def test_search_rejects_non_owner(
    sqlite_session: Session, seeded_kb: tuple[int, int, int]
) -> None:
    kb_id, _, _ = seeded_kb

    with pytest.raises(HTTPException) as exc_info:
        RagService(sqlite_session).search(kb_id, user_id=2, query="anything")

    assert exc_info.value.status_code == 404


def test_query_grounds_answer_and_attaches_sources(
    sqlite_session: Session, seeded_kb: tuple[int, int, int]
) -> None:
    kb_id, _, _ = seeded_kb

    response = RagService(sqlite_session).query(
        kb_id, user_id=1, question="三月销售额为什么异常"
    )

    assert "离线 mock" in response.answer, "mock LLM output must be labelled"
    assert "异常峰值" in response.answer
    assert response.sources, "answers must carry citations"
    assert response.sources[0].filename == DOC2_NAME
    assert response.sources[0].page_number == 3
    assert response.llm_provider == "mock"
    assert response.duration_ms >= 0


def test_query_on_empty_kb_reports_no_evidence(
    sqlite_session: Session,
) -> None:
    owner = User(username="empty", email="empty@example.com", hashed_password="x")
    sqlite_session.add(owner)
    sqlite_session.commit()
    kb = KnowledgeBase(user_id=owner.id, name="空知识库")
    sqlite_session.add(kb)
    sqlite_session.commit()

    response = RagService(sqlite_session).query(kb.id, user_id=owner.id, question="任何问题")

    assert response.sources == []
    assert "没有找到" in response.answer or "没有检索到" in response.answer


def test_retrieval_pipeline_respects_final_top_k(
    sqlite_session: Session, seeded_kb: tuple[int, int, int]
) -> None:
    kb_id, _, _ = seeded_kb

    chunks = RetrievalPipeline(sqlite_session).retrieve(
        kb_id, "机器学习 深度学习 数据 模型 检索 客户", final_top_k=2
    )

    assert len(chunks) == 2
    scores = [chunk.final_score for chunk in chunks]
    assert scores == sorted(scores, reverse=True)


def test_retrieval_skips_chunks_without_embeddings(
    sqlite_session: Session, seeded_kb: tuple[int, int, int]
) -> None:
    """A document that never got embedded must be invisible to search."""
    kb_id, _, _ = seeded_kb
    orphan = Document(
        knowledge_base_id=kb_id, user_id=1, filename="未嵌入.txt",
        file_type="txt", file_size=10, storage_path="1/c.txt",
        status="completed", chunk_count=1,
    )
    sqlite_session.add(orphan)
    sqlite_session.commit()
    sqlite_session.add(
        DocumentChunk(
            document_id=orphan.id, knowledge_base_id=kb_id, chunk_index=0,
            content="机器学习 完全相同的内容", token_estimate=10,
            page_number=1, embedding=None,
        )
    )
    sqlite_session.commit()

    response = RagService(sqlite_session).search(
        kb_id, user_id=1, query="机器学习 完全相同的内容", top_k=10
    )

    assert all(hit.filename != "未嵌入.txt" for hit in response.hits)
