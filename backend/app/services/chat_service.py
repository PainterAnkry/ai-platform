"""Chat business logic: conversation CRUD + SSE streaming pipeline.

The stream produces typed events, serialised as Server-Sent Events by the
router:

    meta     -> conversation/message bookkeeping (once, first)
    sources  -> retrieved citations for this answer (RAG conversations)
    delta    -> one text chunk of the streaming answer
    done     -> final event with the persisted assistant message id
    error    -> provider failure, stream aborts afterwards

The user message is persisted *before* the stream starts, so a broken
connection never loses the user's input; the assistant message is persisted
when the generator finishes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, AsyncIterator, Optional, Sequence

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.chat import Conversation, Message
from app.rag.llm import ContextBlock, LLMError, get_llm_provider
from app.rag.retrieval import RetrievalPipeline
from app.schemas.chat import (
    ConversationDetailRead,
    ConversationRead,
    MessageRead,
)
from app.schemas.rag import RetrievedSource
from app.services.knowledge_base_service import KnowledgeBaseService

logger = logging.getLogger(__name__)

# How many previous messages feed back into the prompt.
HISTORY_WINDOW = 10

CHAT_PERSONA = (
    "你是 AI Platform 的中文智能助手，回答简洁、准确，代码用 Markdown 代码块包裹。"
)

MAX_TITLE_LENGTH = 60


@dataclass
class ChatStreamEvent:
    """One SSE event before serialisation."""

    event: str
    data: dict[str, Any]


class ChatService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.kb_service = KnowledgeBaseService(db)

    # ---------- conversation CRUD ----------
    def create_conversation(
        self,
        user_id: int,
        *,
        title: Optional[str] = None,
        knowledge_base_id: Optional[int] = None,
        agent_id: Optional[int] = None,
    ) -> ConversationRead:
        if knowledge_base_id is not None:
            # 404 when the KB is not the caller's.
            self.kb_service.get_for_user(knowledge_base_id, user_id)
        if agent_id is not None:
            from app.services.agent_service import AgentService

            AgentService(self.db).get_for_user(agent_id, user_id)
        conv = Conversation(
            user_id=user_id,
            title=(title or "").strip()[:128] or "新对话",
            knowledge_base_id=knowledge_base_id,
            agent_id=agent_id,
        )
        self.db.add(conv)
        self.db.commit()
        self.db.refresh(conv)
        return ConversationRead.model_validate(conv)

    def list_conversations(self, user_id: int) -> list[ConversationRead]:
        stmt = (
            select(Conversation)
            .where(Conversation.user_id == user_id)
            .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
            .limit(100)
        )
        return [
            ConversationRead.model_validate(conv)
            for conv in self.db.execute(stmt).scalars().all()
        ]

    def get_for_user(self, conv_id: int, user_id: int) -> Conversation:
        conv = self.db.get(Conversation, conv_id)
        if conv is None or conv.user_id != user_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Conversation not found",
            )
        return conv

    def get_detail(self, conv_id: int, user_id: int) -> ConversationDetailRead:
        conv = self.get_for_user(conv_id, user_id)
        detail = ConversationDetailRead.model_validate(conv)
        detail.messages = self.list_messages(conv_id, user_id)
        return detail

    def list_messages(self, conv_id: int, user_id: int) -> list[MessageRead]:
        self.get_for_user(conv_id, user_id)
        stmt = (
            select(Message)
            .where(Message.conversation_id == conv_id)
            .order_by(Message.id)
        )
        return [
            MessageRead.model_validate(msg)
            for msg in self.db.execute(stmt).scalars().all()
        ]

    def rename(self, conv_id: int, user_id: int, title: str) -> ConversationRead:
        conv = self.get_for_user(conv_id, user_id)
        conv.title = title.strip()[:128]
        self.db.commit()
        self.db.refresh(conv)
        return ConversationRead.model_validate(conv)

    def delete(self, conv_id: int, user_id: int) -> None:
        conv = self.get_for_user(conv_id, user_id)
        self.db.delete(conv)
        self.db.commit()

    # ---------- streaming chat ----------
    async def stream_chat(
        self,
        conv_id: int,
        user_id: int,
        content: str,
        images: Optional[list[str]] = None,
    ) -> AsyncIterator[ChatStreamEvent]:
        """Yield typed chat events for one user message.

        Persists the user message up front; the assistant reply is written
        when the stream completes. Ownership is checked before the first
        event so errors surface as normal HTTP responses.
        """
        conv = self.get_for_user(conv_id, user_id)

        user_msg = Message(
            conversation_id=conv.id, role="user", content=content
        )
        self.db.add(user_msg)
        self._autotitle(conv, content)
        self.db.commit()
        self.db.refresh(user_msg)

        yield ChatStreamEvent(
            "meta",
            {
                "conversation_id": conv.id,
                "title": conv.title,
                "user_message_id": user_msg.id,
            },
        )

        # ---------- agent mode (Phase 5 tool loop) ----------
        agent = None
        if conv.agent_id is not None:
            from app.models.agent import Agent

            agent = self.db.get(Agent, conv.agent_id)
        if agent is not None:
            # A deleted agent silently degrades to plain chat.
            async for event in self._stream_agent_chat(
                conv, agent, content, user_id
            ):
                yield event
            return

        # ---------- optional RAG grounding ----------
        blocks: list[ContextBlock] = []
        sources_payload: list[dict[str, Any]] = []
        if conv.knowledge_base_id is not None:
            try:
                pipeline = RetrievalPipeline(self.db)
                chunks = pipeline.retrieve(
                    conv.knowledge_base_id,
                    content,
                    final_top_k=settings.rag_final_top_k,
                )
            except Exception as exc:  # noqa: BLE001 - reported as SSE error
                logger.warning("Retrieval failed for conv %s: %s", conv.id, exc)
                chunks = []
            sources_payload = [
                _source_payload(chunk, index=rank)
                for rank, chunk in enumerate(chunks, 1)
            ]
            blocks = [
                ContextBlock(index=rank, source=chunk.source, content=chunk.content)
                for rank, chunk in enumerate(chunks, 1)
            ]
            yield ChatStreamEvent("sources", {"sources": sources_payload})

        # ---------- prompt: persona + history + current question ----------
        llm = get_llm_provider()
        llm_messages = self._build_llm_messages(conv.id, content, blocks, images)

        pieces: list[str] = []
        try:
            if blocks:
                # RAG grounding keeps the numbered-snippet prompt; images are
                # a plain-chat feature in this iteration.
                stream = llm.stream_rag_answer(content, blocks)
            else:
                stream = llm.stream_complete(llm_messages)
            async for delta in stream:
                pieces.append(delta)
                yield ChatStreamEvent("delta", {"text": delta})
        except LLMError as exc:
            logger.warning("LLM stream failed for conv %s: %s", conv.id, exc)
            yield ChatStreamEvent("error", {"detail": str(exc)})
            return

        answer = "".join(pieces)
        assistant = Message(
            conversation_id=conv.id,
            role="assistant",
            content=answer,
            sources=sources_payload or None,
            model=llm.signature,
        )
        self.db.add(assistant)
        self.db.commit()
        self.db.refresh(assistant)

        yield ChatStreamEvent(
            "done",
            {
                "assistant_message_id": assistant.id,
                "model": llm.signature,
                "sources": sources_payload,
            },
        )

    # ---------- internals ----------
    async def _stream_agent_chat(
        self,
        conv: Conversation,
        agent: Any,
        content: str,
        user_id: int,
    ) -> AsyncIterator[ChatStreamEvent]:
        """Run the Phase 5 tool loop and persist the assistant outcome."""
        from app.agent.executor import AgentRunResult, build_agent_env, run_agent_stream
        from app.agent.tools import ToolContext
        from app.models.agent import DataFile

        data_files = (
            self.db.execute(
                select(DataFile).where(DataFile.user_id == user_id)
            )
            .scalars()
            .all()
        )
        ctx = ToolContext(
            db=self.db,
            user_id=user_id,
            knowledge_base_id=conv.knowledge_base_id,
            data_files={row.id: row for row in data_files},
        )
        history = [
            {"role": msg.role, "content": msg.content}
            for msg in self._recent_history(conv.id)
            if msg.content.strip() and msg.role in ("user", "assistant")
        ]

        result = AgentRunResult()
        async for event in run_agent_stream(
            ctx,
            question=content,
            history=history,
            tool_names=agent.tools,
            system_prompt=agent.system_prompt or "",
            env=build_agent_env(conv.knowledge_base_id, data_files),
            result=result,
        ):
            yield event

        assistant = Message(
            conversation_id=conv.id,
            role="assistant",
            content=result.answer,
            sources=result.sources or None,
            tool_calls={
                "steps": result.steps,
                "attachments": result.attachments,
            },
            model=result.model or None,
        )
        self.db.add(assistant)
        self.db.commit()
        self.db.refresh(assistant)

        yield ChatStreamEvent(
            "done",
            {
                "assistant_message_id": assistant.id,
                "model": result.model,
                "sources": result.sources,
                "attachments": result.attachments,
                "steps": result.steps,
            },
        )

    def _recent_history(self, conv_id: int) -> list[Any]:
        stmt = (
            select(Message)
            .where(Message.conversation_id == conv_id)
            .order_by(Message.id.desc())
            .limit(HISTORY_WINDOW)
        )
        return list(reversed(self.db.execute(stmt).scalars().all()))

    def _autotitle(self, conv: Conversation, first_user_content: str) -> None:
        """Title a fresh conversation from its first user message."""
        if conv.title != "新对话":
            return
        conv.title = first_user_content.strip()[:MAX_TITLE_LENGTH] or "新对话"

    def _build_llm_messages(
        self,
        conv_id: int,
        current_question: str,
        blocks: Sequence[ContextBlock],
        images: Optional[list[str]] = None,
    ) -> list[dict[str, Any]]:
        """System persona + last HISTORY_WINDOW messages + current question.

        With attachments the current user message uses OpenAI's multimodal
        array form so vision-capable providers receive the images; text-only
        providers just see the text item.
        """
        history = [
            msg
            for msg in self._recent_history(conv_id)
            if msg.role in ("user", "assistant")
        ]

        messages: list[dict[str, Any]] = [{"role": "system", "content": CHAT_PERSONA}]
        for msg in history:
            if msg.content.strip():
                messages.append({"role": msg.role, "content": msg.content})

        if images:
            parts: list[dict[str, Any]] = [{"type": "text", "text": current_question}]
            for image in images:
                parts.append({"type": "image_url", "image_url": {"url": image}})
            current = {"role": "user", "content": parts}
            if messages and messages[-1]["role"] == "user":
                messages[-1] = current
            else:
                messages.append(current)
            return messages

        if not messages or messages[-1] != {
            "role": "user",
            "content": current_question,
        }:
            messages.append({"role": "user", "content": current_question})
        return messages


def _source_payload(chunk: Any, *, index: int) -> dict[str, Any]:
    """Serialise a retrieved chunk exactly like the Phase 3 /query endpoint."""
    source = RetrievedSource(
        index=index,
        document_id=chunk.document_id,
        chunk_id=chunk.chunk_id,
        filename=chunk.filename,
        page_number=chunk.page_number,
        chunk_index=chunk.chunk_index,
        vector_score=round(chunk.vector_score, 6),
        rerank_score=(
            round(chunk.rerank_score, 6) if chunk.rerank_score is not None else None
        ),
        final_score=round(chunk.final_score, 6),
        snippet=chunk.snippet,
    )
    return source.model_dump()


def count_conversations(db: Session, user_id: int) -> int:
    return int(
        db.execute(
            select(func.count(Conversation.id)).where(
                Conversation.user_id == user_id
            )
        ).scalar_one()
    )


def count_ai_calls(db: Session, user_id: int) -> int:
    """Assistant messages across the user's conversations."""
    return int(
        db.execute(
            select(func.count(Message.id))
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(Conversation.user_id == user_id, Message.role == "assistant")
        ).scalar_one()
    )
